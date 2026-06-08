# ai-hub — LAN内ローカルLLM推論ハブ 設計メモ

最終更新: 2026-06-01

---

## 1. 目的とスコープ

このマシン(GPU搭載)を **LAN内の汎用ローカル推論サービス**にする。特定プロジェクトに属さず、複数のクライアント(self-knowledge 等)が**OpenAI互換API**で叩いて要約・分類・タグ付け・埋め込みなどを得る、共有の「AI推論ハブ」。

**やること**
- 1つの安定したエンドポイントの裏に、ローカルLLM(生成)と埋め込みを束ねて提供する。
- LAN内の任意のクライアントから、APIキー付きで利用できる。
- 常時稼働・自動復帰。モデルは常駐させ低レイテンシで応答。

**やらないこと(スコープ外)**
- アプリ固有のロジック(取り込み・推薦・git 等)は**各クライアント側**の責務。ハブは「推論を返す」だけ。
- クラウドLLM(Antigravity CLI / agy 等)の代理はしない。agy はホスト側で別管理(本ハブとは無関係)。
- インターネット公開はしない(§5)。

---

## 2. 全体構成

| レイヤー | 採用 | 備考 |
|---|---|---|
| 実行マシン | Windows + GPU(NVIDIA RTX 5060 Ti 16GB / Blackwell, CUDA 13.1) | 帯域 448GB/s。14Bクラスまで快適 |
| コンテナ基盤 | Docker Desktop(WSL2)+ NVIDIA Container Toolkit | GPUパススルー |
| 推論ランタイム | **ollama**(OpenAI互換API, 動的モデル管理) | container 1。GPU割当 |
| ゲートウェイ | **LiteLLM Proxy**(認証/ルーティング/ログ) | container 2。LAN公開。自作しない |
| 生成モデル | **Qwen3 14B**(品質優先・常駐) | 日本語良好。量子化は品質ノブ |
| 埋め込みモデル | **bge-m3**(多言語) | ollama標準。JP良好 |
| 公開範囲 | **プライベートLAN内のみ** | 実アクセス制御は APIキー |

### データフロー(概要)

```
[LAN内クライアント(self-knowledge worker 他)]
        │  POST /v1/chat/completions  (Authorization: Bearer <key>, model: "quality")
        ▼
┌──────────────── このマシン(Docker Desktop / WSL2) ────────────────┐
│  ┌──────────────┐        ┌──────────────────────────────┐          │
│  │  gateway      │  ─────▶│  ollama (GPU)                │          │
│  │ (LiteLLM)     │        │  ・Qwen3 14B 常駐(生成)     │          │
│  │ :8080 → :4000 │◀─────  │  ・bge-m3(埋め込み)         │          │
│  │ ・APIキー認証 │        │  ・モデルは VRAM 常駐         │          │
│  │ ・論理名解決  │        └──────────────────────────────┘          │
│  │ ・ログ        │            ▲ GPU 1枚を占有                       │
│  └──────────────┘            ollama_models ボリュームに重み永続      │
└──────────────────────────────────────────────────────────────────┘
```

クライアントは **論理モデル名(`quality` / `embed`)** だけを知る。実体(`qwen3:14b` 等)はゲートウェイの設定で隠蔽し、後から差し替えてもクライアントは無変更。

---

## 3. コンポーネントと役割

| コンポーネント | 役割 | GPU | 公開 |
|---|---|---|---|
| **ollama** | モデルの常駐・推論。OpenAI互換 `:11434`。重みは名前付きボリュームに永続 | ✅ 占有 | コンテナ内ネットワークのみ(直接公開しない) |
| **gateway (LiteLLM)** | 唯一の外部窓口。APIキー認証・論理モデル名のルーティング・使用ログ・OpenAI互換の統一契約 | ─ | LAN `:8080` |

**設計上の要点**
- ollama 自体がAPIを持つが、**直接公開しない**。認証もルーティングもログも無い素のランタイムをLANに晒さないため、必ずゲートウェイ経由にする。
- ゲートウェイは「素通しラッパ」にしない。**認証・論理名・ログという横断機能を担って初めて2コンテナ構成の意味が出る**。

---

## 4. モデル戦略と VRAM 配分

**方針:品質優先。Qwen3 14B を常駐**(`OLLAMA_KEEP_ALIVE=-1`)させ、モデル退避によるコールドスタートを避ける。

### 実測スループット(参考, Q4量子化)

| モデル | 速度 | VRAM目安 | 位置づけ |
|---|---|---|---|
| Qwen3 14B Q4_K_M | ~31–33 tok/s(16k文脈) | ~9–10GB | **採用(品質)** |
| Llama 3.1 8B Q4 | ~58 tok/s | ~6GB | 速度版の候補(将来 `fast` 別名) |
| bge-m3(埋め込み) | ― | ~1–2GB | 埋め込み常駐 |

### VRAM 配分(16GB)

| 常駐させるもの | 概算 | 合計 |
|---|---|---|
| Qwen3 14B(Q4_K_M) | ~10GB | |
| bge-m3 | ~2GB | **~12GB** |
| 余白(KV/文脈拡張) | ~4GB | 16GB |

- **品質ノブ**:余白がある分、量子化を **Q5/Q6** に上げると品質向上(VRAM増)。`qwen3:14b` 既定はQ4_K_M。
- **⚠️ Whisper は本ハブに常駐させない**。14B + 埋め込みで余白が薄く、文字起こしは別(クライアント側 or オンデマンド)が無難。
- 「色々な要望」でモデルが増えると VRAM が律速。**常駐は最小限(生成1 + 埋め込み1)**を原則とし、追加はオンデマンド・ロード(コールドスタート許容)で。

---

## 5. ネットワークとセキュリティ

- **公開範囲:プライベートLAN内のみ。インターネット非公開。**
- **実アクセス制御 = APIキー(LiteLLM master key)**。鍵が無ければ叩けない。
- ゲートウェイは LAN にポート公開(`8080`)。ollama は**公開しない**(コンテナ内ネットワークのみ)。
- 「LAN内のみ」の担保は運用責任:
  - ルータでポート転送しない / Windows Firewall でWAN遮断。
  - さらに絞るなら、ゲートウェイを **特定LAN IP にバインド**(`"192.168.x.y:8080:4000"`)。
- master key は `.env` で管理し、リポジトリにコミットしない(`.gitignore`)。

---

## 6. API 契約(クライアント向け)

**OpenAI互換**。クライアントは論理モデル名のみ参照する。

- ベースURL: `http://<このマシンのLAN IP>:8080`
- 認証: `Authorization: Bearer <LITELLM_MASTER_KEY>`
- 生成: `POST /v1/chat/completions`, `model: "quality"`
- 埋め込み: `POST /v1/embeddings`, `model: "embed"`

リクエスト例:
```
POST /v1/chat/completions
Authorization: Bearer sk-xxxx
{
  "model": "quality",
  "messages": [{"role": "user", "content": "<本文> を3行で要約し JSON で返せ"}]
}
```

**論理モデル名(契約の安定点)**

| 論理名 | 実体(差し替え可) | 用途 |
|---|---|---|
| `quality` | `qwen3:14b` | 要約・分類・タグ・プロファイル更新など生成全般 |
| `embed` | `bge-m3` | 類似検索・関連リンク用ベクトル |
| (将来) `fast` | `llama3.1:8b` 等 | 速度優先の軽量タスク |

---

## 7. デプロイ(docker-compose 骨子)

**`docker-compose.yaml`**
```yaml
services:
  ollama:                          # container 1: 推論ランタイム(GPU)
    image: ollama/ollama:latest
    container_name: ai-hub-ollama
    restart: unless-stopped
    environment:
      - OLLAMA_KEEP_ALIVE=-1       # 14Bを常駐(品質/低レイテンシ)
      - OLLAMA_HOST=0.0.0.0
    volumes:
      - ollama_models:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks: [aihub]              # ポート非公開=ゲートウェイ経由でのみ到達

  gateway:                         # container 2: LiteLLM Proxy(認証/ルーティング)
    image: ghcr.io/berriai/litellm:main-latest
    container_name: ai-hub-gateway
    restart: unless-stopped
    depends_on: [ollama]
    ports:
      - "8080:4000"                # LAN公開(必要なら "192.168.x.y:8080:4000")
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
    volumes:
      - ./litellm.config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    networks: [aihub]

volumes:
  ollama_models:
networks:
  aihub:
```

**`litellm.config.yaml`**
```yaml
model_list:
  - model_name: quality
    litellm_params:
      model: ollama_chat/qwen3:14b
      api_base: http://ollama:11434
  - model_name: embed
    litellm_params:
      model: ollama/bge-m3
      api_base: http://ollama:11434

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
litellm_settings:
  drop_params: true
```

**`.env`**(コミットしない)
```
LITELLM_MASTER_KEY=sk-<長いランダム文字列>
```

**自動起動:** Docker Desktop を「ログオン時起動」+ 各サービス `restart: unless-stopped`。マシン起動 → Docker 起動 → スタック復帰で完結(Windows Task Scheduler は使わない)。

**GPUパススルー(前提確認):** Docker Desktop(WSL2)+ NVIDIA Container Toolkit。初回に `docker run --rm --gpus all ollama/ollama` で疎通を確認してから compose を上げる。GPUは Blackwell・CUDA 13.1。

---

## 8. 運用

- **初回モデル取得(compose では自動化されない)**:
  ```
  docker exec ai-hub-ollama ollama pull qwen3:14b
  docker exec ai-hub-ollama ollama pull bge-m3
  ```
- **常駐確認**:`docker exec ai-hub-ollama ollama ps`(モデルが loaded のまま=keep-alive 効いている)。
- **ヘルス**:`GET http://<LAN IP>:8080/health`(LiteLLM)/ ゲートウェイのログで使用量・エラーを確認。
- **更新**:`docker compose pull && docker compose up -d`。モデル更新は `ollama pull`。
- **重みの永続**:`ollama_models` ボリューム。喪失時は再 pull で復旧(GitHubバックアップ等は不要、重みは再取得可能)。

---

## 9. クライアント統合

クライアント(例:self-knowledge worker)は**ハブのクライアントに徹する**:

- env で2つだけ持つ:
  ```
  AI_HUB_URL=http://<このマシンのLAN IP>:8080
  AI_HUB_KEY=sk-xxxx
  ```
- OpenAI互換SDK or 素のHTTPで `model: "quality"` / `"embed"` を叩く。
- ハブのモデル差し替え(`quality` の実体変更・量子化変更)に**クライアントは追従不要**。

---

## 10. 設計原則

1. **ハブは推論だけを返す。** アプリ固有ロジックは持たない。
2. **ランタイムを直接LANに晒さない。** 必ず認証付きゲートウェイ経由。
3. **論理モデル名で隔離。** 実体・量子化・モデルの差し替えはゲートウェイ設定に閉じ、クライアントを壊さない。
4. **常駐は最小限。** VRAM が律速。生成1 + 埋め込み1 を常駐、追加はオンデマンド。
5. **鍵で守り、ネットワークで漏らさない。** master key 必須・インターネット非公開。

---

## 11. 今後詰める項目(未確定)

- Qwen3 14B の**量子化レベル**の最終決定(品質と VRAM 余白のバランス。Q4_K_M / Q5 / Q6)。
- 埋め込みモデルの確定(`bge-m3` vs `multilingual-e5-large`)と、クライアント側の次元・正規化の取り決め。
- 同時要求時の挙動(GPU単一=実質直列)。キューイング/タイムアウトの方針と、`fast`(8B)別名の導入要否。
- ゲートウェイのキー運用(クライアントごとに発行・失効する virtual key を使うか、master key 単一で済ますか)。
- 監視/ログの保存先と保持期間(LiteLLM のログ出力をどこに溜めるか)。
- Whisper を将来ハブに同居させるか、クライアント側に置くかの最終判断(VRAM 配分次第)。
