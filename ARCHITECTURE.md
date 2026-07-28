# ai-hub — LAN内ローカルLLM推論ハブ 設計 & 連携手順書

最終更新: 2026-07-28

このドキュメントは 2 つの役割を持つ。

- **§1–§4, §8–§12**: ハブ自体の設計メモ(なぜこの構成か、VRAM をどう配分するか)。
- **§5–§7**: **このハブを使いたい別アプリケーションのための連携手順書**。実装者はここだけ読めば繋がる。

より詳しいクライアント向けサンプルコード・エラー早見表は [ai-hub-client-guide.md](ai-hub-client-guide.md) を参照。

---

## 1. 目的とスコープ

このマシン(GPU搭載)を **LAN内の汎用ローカル推論サービス**にする。特定プロジェクトに属さず、複数のクライアントが**OpenAI互換API**で叩いて要約・分類・タグ付け・埋め込みなどを得る、共有の「AI推論ハブ」。

**やること**
- 1つの安定したエンドポイントの裏に、ローカルLLM(生成)と埋め込みを束ねて提供する。
- LAN内の任意のクライアントから、APIキー付きで利用できる。
- 常時稼働・自動復帰。モデルは常駐させ低レイテンシで応答。
- **「今どのモデルが実際に使えるか」を API で返す**(§5)。

**やらないこと(スコープ外)**
- アプリ固有のロジック(取り込み・推薦・git 等)は**各クライアント側**の責務。ハブは「推論を返す」だけ。
- クラウドLLMの代理はしない。
- インターネット公開はしない(§8)。
- **会話履歴を持たない**。ステートレス。履歴はクライアントが保持し毎回まるごと送る。

---

## 2. 全体構成

| レイヤー | 採用 | 備考 |
|---|---|---|
| 実行マシン | Windows 11 + NVIDIA RTX 5060 Ti 16GB (Blackwell) | 帯域 448GB/s |
| コンテナ基盤 | Docker Desktop(WSL2)+ NVIDIA Container Toolkit | GPUパススルー |
| 推論ランタイム | **ollama**(OpenAI互換API, 動的モデル管理) | GPU占有 |
| ゲートウェイ | **LiteLLM Proxy**(認証/ルーティング/ログ) | 唯一の外部窓口。自作しない |
| カタログ | **自作の極小HTTPサービス**(Python標準ライブラリのみ) | 「実際に呼べるモデル」を返す |
| 生成モデル | **Qwen3 14B** / **Qwen3.5 9B** | 日本語良好。§4 |
| 埋め込みモデル | **bge-m3**(多言語, 1024次元) | ollama標準。JP良好 |
| 公開範囲 | **プライベートLAN内のみ** | 実アクセス制御は APIキー |

### コンテナ(4つ)

すべて [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) で定義。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 推論ランタイム(GPU占有) | `11434`(内部のみ) | 非公開 |
| `ai-hub-catalog` | モデルカタログAPI。実行可能なモデルだけを返す | `8080`(内部のみ) | 非公開(gateway経由) |
| `ai-hub-gateway` | LiteLLM Proxy。**唯一の外部窓口**。認証/論理名ルーティング/ログ | host `20800`→`4000` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI | host `20801`→`8000` | LAN公開 |

ホスト公開ポートは **20800-20899** を local-ai-hub 用に予約(Notion ポート台帳)。現在の使用は 20800 / 20801。

### データフロー

```
[LAN内クライアント(別アプリ)]
        │  GET  /v1/catalog          ← 使えるモデルを問い合わせ
        │  POST /v1/chat/completions (Authorization: Bearer <key>, model: "quality")
        ▼
┌──────── このマシン(Docker Desktop / WSL2) ─────────────────────────┐
│  ┌──────────────┐                                                   │
│  │  gateway     │───────────────▶┌──────────────────────────┐       │
│  │  (LiteLLM)   │                │  ollama (GPU)            │       │
│  │ :20800→:4000 │◀───────────────│  ・生成モデル(常駐)      │       │
│  │ ・APIキー認証│                │  ・bge-m3(埋め込み)      │       │
│  │ ・論理名解決 │                └──────────────────────────┘       │
│  │ ・ログ       │                     ▲ GPU 1枚を占有                │
│  │              │  /v1/catalog        │  ollama_models に重み永続    │
│  │  pass-through│──▶┌─────────────┐   │                              │
│  └──────────────┘   │  catalog    │───┘ /api/tags で pull 済みを確認 │
│                     │  :8080      │──▶ gateway /model/info で        │
│                     └─────────────┘    ルーティング定義を確認        │
└────────────────────────────────────────────────────────────────────┘
```

クライアントは **論理モデル名(`quality` / `quality-next` / `embed`)** だけを知る。実体(`qwen3:14b` 等)はゲートウェイ設定で隠蔽し、後から差し替えてもクライアントは無変更。

---

## 3. コンポーネントと役割

| コンポーネント | 役割 | GPU | 公開 |
|---|---|---|---|
| **ollama** | モデルの常駐・推論。重みは名前付きボリュームに永続 | ✅ 占有 | コンテナ内のみ(直接公開しない) |
| **gateway (LiteLLM)** | 唯一の外部窓口。APIキー認証・論理名ルーティング・使用ログ・OpenAI互換の統一契約 | ─ | LAN `:20800` |
| **catalog** | gateway の `/model/info` と ollama の `/api/tags` を突合し、**実際に呼べるモデルだけ**を返す | ─ | gateway の pass-through 経由のみ |
| **chat** | 動作確認用の薄いUI。モデル選択肢はカタログAPIから動的取得 | ─ | LAN `:20801` |

**設計上の要点**
- ollama 自体がAPIを持つが、**直接公開しない**。認証もルーティングもログも無い素のランタイムをLANに晒さない。
- ゲートウェイは「素通しラッパ」にしない。**認証・論理名・ログという横断機能**を担って初めて分離の意味が出る。
- catalog も **host にポートを開かない**。外部からは gateway の `/v1/catalog` 経由でのみ到達する。外部窓口を1つに保つため。

---

## 4. モデル戦略と VRAM 配分

**方針:品質優先。生成モデル1つを常駐**(`OLLAMA_KEEP_ALIVE=-1`)させ、コールドスタートを避ける。

### 現在インストール済みのモデル

| 論理名 | 実体 | サイズ | 位置づけ |
|---|---|---|---|
| `quality` | `qwen3:14b` (Q4_K_M) | 9.3GB | 現行の品質優先モデル(thinking) |
| `quality-next` | `qwen3.5:9b` | 6.6GB | 後継候補。日本語スコアで旧14Bを上回る(thinking) |
| `embed` | `bge-m3` | 1.2GB | 埋め込み(1024次元)・常駐 |

### VRAM 実測(16GB = 16311 MiB, デスクトップアプリ込みの総使用量)

| 常駐構成 | 使用量 | 空き | 評価 |
|---|---|---|---|
| `quality`(11GB) + `embed`(664MB) | **15526 MiB** | 785 MB | ⚠️ ほぼ上限。ブラウザ等のGPU使用が増えると `cudaMalloc failed: out of memory` で 500 になる(実際に発生) |
| `quality-next`(6.3GB) + `embed`(664MB) | **11259 MiB** | 5052 MB | ✅ 余裕あり |

- **コンテキスト長**:`OLLAMA_CONTEXT_LENGTH=32768`。既定 4096 では thinking モデルが途中打ち切りになるため引き上げ済み。`OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1` で KV キャッシュを量子化して 16GB に収める。
- **生成モデルは実質1つしか常駐できない**。別の生成モデルを要求すると ollama が LRU を退避して積み替える(コールドスタート数十秒)。VRAM に余裕が無いと退避が間に合わず OOM になる。
- **常駐は最小限(生成1 + 埋め込み1)**を原則とし、追加はオンデマンド。
- **⚠️ Whisper は本ハブに常駐させない**。VRAM 余白が薄い。

---

## 5. モデルカタログ API(どのモデルが使えるか)

### 5.1 なぜ `/v1/models` では不十分か

LiteLLM 標準の `GET /v1/models` は **`litellm.config.yaml` に書かれた論理名を返すだけ**で、その実体が ollama に pull 済みかは見ていない。そのため:

- 未 pull のモデルが一覧に載る → 呼ぶと `500 model 'xxx' not found`
- ボリューム喪失(既知の障害モード)で重みが消えても一覧は変わらない

実際に `fast` / `gemma` がこの状態だった(一覧に出るが 500)。

### 5.2 `GET /v1/catalog` — 一覧されたものは必ず呼べる

catalog サービスが 2 つの権威情報を突合して返す:

1. gateway `/model/info` … 論理名 → 実体 のルーティング定義
2. ollama `/api/tags` … 実際に pull 済みの実体

```
GET http://192.168.1.111:20800/v1/catalog
Authorization: Bearer <LITELLM_MASTER_KEY>
```

レスポンス(実際の出力):

```json
{
  "object": "list",
  "data": [
    {
      "id": "quality",
      "object": "model",
      "kind": "chat",
      "endpoint": "/v1/chat/completions",
      "installed": true,
      "backend": "qwen3:14b",
      "backend_size_bytes": 9276198565,
      "purpose": "要約・分類・タグ付け・生成全般(高品質)",
      "context_length": 32768,
      "thinking": {
        "enabled_by_default": true,
        "note": "思考は message.reasoning_content に入る。最終回答は message.content。",
        "disable": "ユーザメッセージ末尾に /no_think を付ける",
        "caution": "max_tokens が小さいと思考の途中で打ち切られ content が空になる(目安 1024 以上)"
      },
      "notes": "VRAM をほぼ使い切るため、GPU が他アプリに使われていると 500(out of memory)になることがある"
    },
    {
      "id": "quality-next",
      "kind": "chat",
      "endpoint": "/v1/chat/completions",
      "installed": true,
      "backend": "qwen3.5:9b",
      "purpose": "日本語の要約・テーマ抽出(quality の後継候補・評価中)",
      "context_length": 32768,
      "thinking": {
        "enabled_by_default": true,
        "disable": "リクエストボディに \"think\": false (または \"reasoning_effort\": \"none\")",
        "caution": "/no_think は効かない。抑制しないと簡単な質問でも 1 万トークン超を思考に費やす"
      }
    },
    {
      "id": "embed",
      "kind": "embedding",
      "endpoint": "/v1/embeddings",
      "installed": true,
      "backend": "bge-m3:latest",
      "purpose": "類似検索・関連リンク用の埋め込みベクトル",
      "dimensions": 1024
    }
  ]
}
```

**フィールドの意味**

| フィールド | 用途 |
|---|---|
| `id` | **リクエストの `model` に渡す論理名。クライアントが使うのはこれだけ。** |
| `kind` | `chat` / `embedding`。呼び分けの判定に使う |
| `endpoint` | そのモデルを呼ぶパス。`kind` から導けるが明示してある |
| `installed` | 既定の一覧では常に `true`(呼べないものは載らない) |
| `backend` | 実体名。**参考情報**。ハブ側で差し替えるのでクライアントは依存しないこと |
| `context_length` | 入力 + 思考 + 出力 の合計上限 |
| `thinking` | 思考モデルか、思考の止め方、注意点。**モデルごとに止め方が違うので必ず読む** |
| `dimensions` | 埋め込みの次元(索引設計に使う) |

**診断用**: `GET /v1/catalog?all=1` は未インストールのものも `installed: false` と `unavailable_reason` 付きで返す。設定に書いたのに使えないモデルを切り分けるときに使う。

```json
{"id": "fast", "installed": false,
 "unavailable_reason": "backend model 'llama3.1:8b' is not pulled on the ollama runtime"}
```

### 5.3 メタデータの出どころ

カタログが返す `purpose` / `thinking` / `dimensions` などは [litellm.config.yaml](l-llm-containers/litellm.config.yaml) の各モデルの `model_info` に書く。**ここがクライアント向け説明の単一の情報源**。モデルを追加・差し替えたら必ず更新する。

---

## 6. API 契約(クライアント向け)

**OpenAI互換**。クライアントは論理モデル名のみ参照する。

- ベースURL: `http://192.168.1.111:20800`(このマシンのLAN IP)
- 認証: `Authorization: Bearer <LITELLM_MASTER_KEY>`

| メソッド | パス | 用途 | 認証 |
|---|---|---|---|
| GET | `/health/liveliness` | 疎通確認 | 不要 |
| GET | `/v1/catalog` | **使えるモデルの一覧(推奨)** | 必要 |
| GET | `/v1/models` | OpenAI標準のモデル一覧(設定に書かれた論理名) | 必要 |
| POST | `/v1/chat/completions` | 生成 | 必要 |
| POST | `/v1/embeddings` | 埋め込み | 必要 |

生成リクエスト例:
```
POST /v1/chat/completions
Authorization: Bearer sk-xxxx
Content-Type: application/json

{
  "model": "quality",
  "messages": [{"role": "user", "content": "<本文> を3行で要約して"}],
  "max_tokens": 2048
}
```

埋め込みリクエスト例:
```
POST /v1/embeddings
{ "model": "embed", "input": "ベクトル化したいテキスト" }
```
→ `data[0].embedding` に **1024次元**の float 配列。

### 必ず押さえる挙動

1. **ステートレス。** サーバは会話を覚えない。履歴は毎回 `messages` に全部入れて送る。
2. **thinking モデルの扱いはモデルごとに違う。** カタログの `thinking` フィールドに従う。
   - `quality`(Qwen3 14B): 末尾に `/no_think`。`max_tokens` は 1024 以上。
   - `quality-next`(Qwen3.5 9B): **`/no_think` は効かない。** ボディに `"think": false` を入れる。
   - 思考は `message.reasoning_content`、最終回答は `message.content` に入る。
3. **GPU 1枚 = 実質直列。** 同時要求は順番待ち。クライアント側で同時実行数を絞る。
4. **モデルを切り替えると積み替えが起きる。** 別の生成モデルを呼ぶと数十秒のコールドスタート。用途ごとにモデルを頻繁に往復させない。
5. **タイムアウトは 60〜120秒以上。** 初回ロード・長文生成で時間がかかる。
6. **LAN内のみ。** インターネット経由では到達しない。

---

## 7. 別アプリからの接続手順

### 手順

1. **鍵をもらう。** ハブ管理者から `LITELLM_MASTER_KEY` を受け取る(値は [l-llm-containers/.env](l-llm-containers/.env)。コミット禁止)。

2. **env に 2 つだけ持つ。**
   ```
   AI_HUB_URL=http://192.168.1.111:20800
   AI_HUB_KEY=sk-xxxx
   ```

3. **疎通を確認する。**
   ```bash
   curl $AI_HUB_URL/health/liveliness                       # キー不要
   curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"
   ```

4. **カタログを見てモデルを選ぶ。** 起動時に一度取得し、`id` と `thinking` の指示を読む。ハードコードしてもよいが、**カタログに無い id は呼ばない**。

5. **OpenAI互換SDK または素のHTTPで叩く。**
   ```python
   from openai import OpenAI
   client = OpenAI(base_url=f"{AI_HUB_URL}/v1", api_key=AI_HUB_KEY)

   # 使えるモデルを確認(OpenAI標準)
   print([m.id for m in client.models.list().data])

   # 要約(思考を止めて速く返す)
   r = client.chat.completions.create(
       model="quality-next",
       messages=[{"role": "user", "content": "……を3行で要約して"}],
       extra_body={"think": False},      # Qwen3.5 の思考抑制
       max_tokens=2048,
   )
   print(r.choices[0].message.content)

   # 埋め込み
   v = client.embeddings.create(model="embed", input="テキスト").data[0].embedding
   assert len(v) == 1024
   ```

### 設計の約束(クライアントが依存してよいこと / いけないこと)

| 依存してよい | 依存してはいけない |
|---|---|
| 論理名 `quality` / `quality-next` / `embed` | 実体名 `qwen3:14b` 等(`backend` は参考情報) |
| OpenAI互換のリクエスト/レスポンス形状 | 特定の量子化レベル・速度 |
| 埋め込み次元 1024(変更時は再索引が要る旨を通知) | モデルの内部的な思考トークン数 |

### チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を env 化(鍵をコードに埋めない)
- [ ] HTTPタイムアウトを 60〜120秒以上に
- [ ] 同時リクエスト数を絞る(GPU1枚=直列)
- [ ] thinking モデルは `max_tokens` を十分に取り、`content` が空のケースを扱う
- [ ] 埋め込みは次元 **1024** で索引を設計
- [ ] 会話履歴は自前で保持し毎回送る(ハブは覚えない)

---

## 8. ネットワークとセキュリティ

- **公開範囲:プライベートLAN内のみ。インターネット非公開。**
- **実アクセス制御 = APIキー(LiteLLM master key)**。鍵が無ければ叩けない。
  - カタログAPI も同じ鍵。キー無し → 401、不正キー → 400。catalog サービス自身でも Bearer を検証する(gateway の pass-through 認証に依存しきらない多層防御)。
- ゲートウェイのみ LAN にポート公開(`20800`)。**ollama と catalog は公開しない**。
- 「LAN内のみ」の担保は運用責任:
  - ルータでポート転送しない / Windows Firewall でWAN遮断。
  - さらに絞るなら `"192.168.x.y:20800:4000"` で特定LAN IP にバインド。
- master key は `.env` で管理し、リポジトリにコミットしない(`.gitignore` 済み)。

---

## 9. デプロイ

定義は [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) / [litellm.config.yaml](l-llm-containers/litellm.config.yaml) が正。ここでは要点のみ。

```powershell
cd l-llm-containers
docker compose up -d                       # 全コンテナ起動
```

- **設定変更後の反映**
  - `litellm.config.yaml` はバインドマウントのため `docker compose up -d` では再読み込みされない。**`docker restart ai-hub-gateway` が必要**。
  - `catalog/server.py` も同様に `docker restart ai-hub-catalog`。
- **自動起動:** Docker Desktop を「ログオン時起動」+ 各サービス `restart: unless-stopped`。
- **GPUパススルー:** Docker Desktop(WSL2)+ NVIDIA Container Toolkit。

---

## 10. 運用

- **モデル取得は手動**(compose では自動化されない):
  ```powershell
  docker exec ai-hub-ollama ollama pull qwen3:14b     # quality
  docker exec ai-hub-ollama ollama pull qwen3.5:9b    # quality-next
  docker exec ai-hub-ollama ollama pull bge-m3        # embed
  ```
- **モデルを追加したら** [litellm.config.yaml](l-llm-containers/litellm.config.yaml) に論理名と `model_info` を書き、gateway を再起動する。**pull せずに書くとカタログには出ないが `/v1/models` には出てしまう**ので、必ず両方揃える。
- **稼働確認**:
  ```powershell
  docker exec ai-hub-ollama ollama list      # 取得済みモデル
  docker exec ai-hub-ollama ollama ps        # ロード状態(CONTEXT列で文脈長)
  nvidia-smi                                 # VRAM
  curl http://localhost:20800/health/liveliness
  curl http://localhost:20800/v1/catalog -H "Authorization: Bearer $KEY"
  ```
- **重みの永続**:`ollama_models` ボリューム。**喪失事例あり**。API不調時はまず `ollama list` を確認し、空なら再 pull。`/v1/catalog?all=1` は喪失を `installed: false` として即座に可視化する。
- **更新**:`docker compose pull && docker compose up -d`。モデル更新は `ollama pull`。

---

## 11. 設計原則

1. **ハブは推論だけを返す。** アプリ固有ロジックは持たない。
2. **ランタイムを直接LANに晒さない。** 必ず認証付きゲートウェイ経由。外部窓口は gateway ただ1つ。
3. **論理モデル名で隔離。** 実体・量子化の差し替えはゲートウェイ設定に閉じ、クライアントを壊さない。
4. **一覧したものは必ず呼べる。** 設定に書いただけの「呼べないモデル」を外部に見せない(§5)。
5. **常駐は最小限。** VRAM が律速。生成1 + 埋め込み1 を常駐、追加はオンデマンド。
6. **鍵で守り、ネットワークで漏らさない。** master key 必須・インターネット非公開。

---

## 12. 今後詰める項目(未確定)

- **`quality` の実体を `qwen3.5:9b` に差し替えるかの最終判断。** 日本語ベンチで上回り、VRAM も 15.5GB → 11.3GB に下がる(OOM リスク解消)。差し替えればクライアントは無変更。並走評価中。
- 差し替え後、空いた VRAM を**コンテキスト長拡張**(32k → 64k 以上)に回すか。Qwen3.5 は 256k 対応。
- `fast`(軽量・高速)枠を復活させるか。`llama3.1:8b` を pull し直すか、`qwen3.5:4b` にするか。
- 埋め込みモデルの確定(`bge-m3` vs `multilingual-e5-large`)。次元変更は**クライアント側の再索引**が要るため慎重に。
- ゲートウェイのキー運用(クライアントごとの virtual key を発行するか、master key 単一で済ますか)。
- 監視/ログの保存先と保持期間。
- 同時要求時のキューイング/タイムアウト方針(GPU単一=実質直列)。
