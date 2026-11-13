# ai-hub — LAN内ローカルLLM推論ハブ 設計 & 連携手順書

最終更新: 2026-11-13(Strata を追加し、生成の主役を `large` に切り替え)

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
| 実行マシン | Windows 11 + Ryzen 9 7950X + RAM 128GB + NVIDIA RTX 5060 Ti 16GB (Blackwell) | GPU 帯域 448GB/s |
| コンテナ基盤 | Docker Desktop(WSL2)+ NVIDIA Container Toolkit | GPUパススルー。`~/.wslconfig` は `memory=72GB` |
| 推論ランタイム(生成) | **Strata**(GPU・RAM・SSD の3層に MoE の専門家を置く推論エンジン) | §4 |
| 推論ランタイム(埋め込み) | **ollama**(OpenAI互換API, 動的モデル管理) | bge-m3 だけを常駐 |
| ゲートウェイ | **LiteLLM Proxy**(認証/ルーティング/ログ) | 推論APIの窓口。自作しない |
| カタログ | **自作の極小HTTPサービス**(Python標準ライブラリのみ) | 「実際に呼べるモデル」を返す |
| 生成モデル | **Qwen3.8-Flash-Next IQ3_S**(125B MoE / アクティブ 6B) | 日本語良好・画像入力対応。§4 |
| 埋め込みモデル | **bge-m3**(多言語, 1024次元) | ollama標準。JP良好 |
| 公開範囲 | **プライベートLAN内のみ** | 実アクセス制御は APIキー |

### コンテナ(6つ)

すべて [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) で定義。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 埋め込みランタイム(bge-m3 を常駐) | `11434`(内部のみ) | 非公開 |
| `ai-hub-strata` | 生成ランタイム(Strata。論理名 `large`)と Web UI | host `20803`→`8080` | LAN公開(API キー必須) |
| `ai-hub-catalog` | モデルカタログAPI。実行可能なモデルだけを返す | `8080`(内部のみ) | 非公開(gateway経由) |
| `ai-hub-gateway` | LiteLLM Proxy。**推論APIの窓口**。認証/論理名ルーティング/ログ | host `20800`→`4000` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI(既定モデル `large`) | host `20801`→`8000` | LAN公開 |
| `ai-hub-openclaw` | OpenClaw(AIエージェント)と管理画面 Control UI。ハブを使うクライアントの1つ | host `20802`→`18789` | LAN公開(トークン+ペアリング) |

ホスト公開ポートは **20800-20899** を local-ai-hub 用に予約(Notion ポート台帳)。現在の使用は 20800 / 20801 / 20802 / 20803。写しは [docs/port-registry.md](docs/port-registry.md)。

### データフロー

```
[LAN内クライアント(別アプリ)]
        │  GET  /v1/catalog          ← 使えるモデルを問い合わせ
        │  POST /v1/chat/completions (Authorization: Bearer <key>, model: "large")
        ▼
┌──────── このマシン(Docker Desktop / WSL2) ──────────────────────────────┐
│  ┌──────────────┐  large   ┌──────────────────────────────┐            │
│  │  gateway     │─────────▶│  strata (GPU + RAM + NVMe)   │◀─ :20803   │
│  │  (LiteLLM)   │◀─────────│  ・Qwen3.8-Flash-Next IQ3_S  │  Web UI    │
│  │ :20800→:4000 │          └──────────────────────────────┘  (キー必須)│
│  │ ・APIキー認証│  embed   ┌──────────────────────────────┐            │
│  │ ・論理名解決 │─────────▶│  ollama (GPU)                │            │
│  │ ・ログ       │◀─────────│  ・bge-m3(埋め込み・常駐)    │            │
│  │              │          └──────────────────────────────┘            │
│  │              │  /v1/catalog      ▲ /api/tags で pull 済みを確認      │
│  │  pass-through│──▶┌─────────────┐─┘                                   │
│  └──────────────┘   │  catalog    │──▶ strata /v1/models で読み込み済みを確認
│                     │  :8080      │──▶ gateway /model/info でルーティング定義を確認
│                     └─────────────┘                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

クライアントは **論理モデル名(`large` / `embed`)** だけを知る。実体(`qwen3.8-flash-next-iq3_s` 等)はゲートウェイ設定で隠蔽し、後から差し替えてもクライアントは無変更。

---

## 3. コンポーネントと役割

| コンポーネント | 役割 | GPU | 公開 |
|---|---|---|---|
| **strata** | 生成モデル(`large`)の推論。重みはボリューム `ai-hub-strata-data`(Docker のデータ領域 = D: の NVMe) | ✅ 共有(VRAM の大半) | LAN `:20803`(API キー必須) |
| **ollama** | 埋め込みモデル(`embed`)の常駐・推論。重みは名前付きボリュームに永続 | ✅ 共有(約 0.7GB) | コンテナ内のみ(直接公開しない) |
| **gateway (LiteLLM)** | 推論APIの窓口。APIキー認証・論理名ルーティング・使用ログ・OpenAI互換の統一契約 | ─ | LAN `:20800` |
| **catalog** | gateway の `/model/info` と ollama の `/api/tags`・strata の `/v1/models` を突合し、**実際に呼べるモデルだけ**を返す | ─ | gateway の pass-through 経由のみ |
| **chat** | 動作確認用の薄いUI。モデル選択肢はカタログAPIから動的取得 | ─ | LAN `:20801` |
| **openclaw** | AIエージェント(ツール実行・セッション・記憶を持つ)と管理画面。推論は `http://gateway:4000/v1` の `large` を呼ぶ。設定は `l-llm-containers/openclaw/openclaw.json`、状態はボリューム `openclaw_state` | ─ | LAN `:20802` |

**設計上の要点**
- ollama 自体がAPIを持つが、**直接公開しない**。認証もルーティングもログも無い素のランタイムをLANに晒さない。
- **strata だけは例外として `:20803` を LAN に出す**(2026-11-13 の裁定)。目的は Web UI(Chat / Monitor)で負荷や動作を見ること。Strata は API キーが常に必須で、キー無しの API 呼び出しは 401 になる。**クライアントアプリは `:20803` を直接呼ばず、gateway の `large` を使う**(論理名・ログ・カタログの恩恵を受けるため)。
- ゲートウェイは「素通しラッパ」にしない。**認証・論理名・ログという横断機能**を担って初めて分離の意味が出る。
- catalog も **host にポートを開かない**。外部からは gateway の `/v1/catalog` 経由でのみ到達する。

---

## 4. モデル戦略と VRAM 配分

**方針:生成は Strata の大型 MoE を1つ常駐、埋め込みは ollama の bge-m3 を常駐。** どちらも起動したまま(コールドスタートなし)にする。

### 公開中のモデル

| 論理名 | ランタイム | 実体 | サイズ | 位置づけ |
|---|---|---|---|---|
| `large` | strata | Qwen3.8-Flash-Next IQ3_S(ISTA-DASLab) | 重み 54.8GB(ボリューム全体 約 87GB) | 生成の既定。128k 文脈。思考は既定で無効 |
| `embed` | ollama | `bge-m3` | 1.2GB | 埋め込み(1024次元)・常駐 |

`quality`(`qwen3:14b`)と `quality-next`(`qwen3.5:9b`)は 2026-11-13 に非公開にした。重みは ollama に残してあり、[litellm.config.yaml](l-llm-containers/litellm.config.yaml) でコメントアウトしている。`large` と同時に GPU に載せると VRAM が足りないため。

### なぜ Strata か

Qwen3.8-Flash-Next は総パラメータ 125B だが、1トークンで動くのは 6B だけの MoE。Strata はよく使う専門家を GPU に、残りを RAM(page-lock)に、n-gram 埋め込みの表(約 27GB)を RAM か SSD に置き分ける。そのため **16GB の GPU でも 125B 級を 50〜78 tok/s で動かせる**。ollama(llama.cpp)の層単位の分割では、この速度は出ない。

### 実測(2026-11-13, IQ3_S, 128k, `reasoning_effort: none`, gateway 経由)

| 項目 | 値 |
|---|---|
| 生成速度 | コード 73.0 / 論理 75.3 / 英文 54.2 / 日本語 48.8 tok/s |
| prefill | 4k トークンで約 1200 tok/s(3.4 秒)、1.5 万トークンで約 1700 tok/s(9.1 秒) |
| VRAM(合計、デスクトップ込み) | 14.1〜14.9 GB / 16.3 GB。Strata の専門家キャッシュ 5.14GiB、bge-m3 0.7GB |
| RAM | Strata 約 52GB(専門家の重み。WSL VM 72GB 中。残りはページキャッシュと他コンテナ) |
| 同時処理 | 1リクエスト(推測デコードの DFlash2 が1件ずつしか扱わないため) |

参考(Phase 1 の比較):Strata 単独で予備枠なしのときは専門家キャッシュ 6.99GiB・生成 50.6〜77.8 tok/s。ollama に quality-next(64k)と bge-m3 を常駐させたままだと、キャッシュは 3.74GiB に縮み、生成は 48.9〜75.0 tok/s。

- **VRAM の予備枠**:`STRATA_SET=vram-reserve-mib=1792`。Strata は既定で空いている VRAM を専門家キャッシュで使い切る。そのため bge-m3 が後から載る余地がなくなり、さらに長文で KV が伸びると GPU メモリが RAM に追い出されて数 tok/s に落ちる(エンジンログに警告が出る)。予備枠を取ると、すべて読み込んだあとの空きは約 1.1GB になる。
- **RAM**:Strata はコンテナにメモリ上限を付けない前提で、WSL VM 全体の RAM から配置を決める。`~/.wslconfig` の `memory=16GB`(旧設定)では起動しない。
  - 専門家の重み(約 51GB)は RAM に page-lock で常駐する。IQ3_S を使う限り、これより減らせない。
  - n-gram 埋め込みの表(26.8GB)は `STRATA_SET` の `STRATA_PLE_AUTO_RAM=0` で NVMe から直接読ませている。既定では RAM に余裕があると表をファイルキャッシュに載せるため、WSL VM が 98GB まで膨らみ、Windows の空きが 3GB まで減った(2026-11-13)。
  - WSL VM の上限は `memory=72GB`(104GB から下げた)。内訳は Strata 約 52GB、他コンテナ約 5GB、残りがページキャッシュ。Windows の空きは約 33GB。
  - 上限を下げた前後で速度はほぼ同じ(生成 48.5〜79.0 tok/s、prefill 1207〜1689 tok/s)。
- **ストレージ**:重みは NVMe 必須(SATA HDD では起動に数分かかり、専門家の読み込みも遅い)。E: は HDD なので、プロジェクト内へのバインドマウントではなく名前付きボリューム(Docker のデータ領域 = D: の NVMe)に置く。
- **ollama のコンテキスト長設定**(`OLLAMA_CONTEXT_LENGTH=32768`、KV q8_0、flash attention)は残してある。非公開にした生成モデルを戻すとき用で、今は bge-m3 にしか効かない。
- **⚠️ Whisper などの GPU モデルを本ハブに追加しない**。VRAM の空きは約 1.1GB しかない。

---

## 5. モデルカタログ API(どのモデルが使えるか)

### 5.1 なぜ `/v1/models` では不十分か

LiteLLM 標準の `GET /v1/models` は **`litellm.config.yaml` に書かれた論理名を返すだけ**で、その実体が ollama に pull 済みかは見ていない。そのため:

- 未 pull のモデルが一覧に載る → 呼ぶと `500 model 'xxx' not found`
- ボリューム喪失(既知の障害モード)で重みが消えても一覧は変わらない

実際に `fast` / `gemma` がこの状態だった(一覧に出るが 500)。

### 5.2 `GET /v1/catalog` — 一覧されたものは必ず呼べる

catalog サービスが 3 つの権威情報を突合して返す:

1. gateway `/model/info` … 論理名 → 実体 のルーティング定義
2. ollama `/api/tags` … 実際に pull 済みの実体
3. strata `/v1/models` … Strata に読み込まれて応答できる実体(`api_base` が strata の論理名だけ)。Strata が停止中・読み込み中なら `large` は一覧から消える

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
      "id": "large",
      "object": "model",
      "kind": "chat",
      "endpoint": "/v1/chat/completions",
      "installed": true,
      "backend": "qwen3.8-flash-next-iq3_s",
      "purpose": "生成全般(要約・抽出・分類・コード・tool call・エージェント実行)。ハブの既定モデル",
      "context_length": 131072,
      "thinking": {
        "enabled_by_default": false,
        "note": "思考を有効にすると message.reasoning_content に入る。最終回答は message.content。",
        "enable": "リクエストボディに \"reasoning_effort\": \"low\" / \"medium\" / \"high\"",
        "disable": "既定で無効(ハブが \"reasoning_effort\": \"none\" を補う)"
      },
      "notes": "生成 50〜78 tok/s、prefill 1200〜1600 tok/s(2026-11-13 実測)。同時に処理するのは1リクエストだけで、後続は待たされる。長文は読み込みに時間がかかる(1.5 万トークンで約 10 秒)"
    },
    {
      "id": "embed",
      "object": "model",
      "kind": "embedding",
      "endpoint": "/v1/embeddings",
      "installed": true,
      "backend": "bge-m3:latest",
      "backend_size_bytes": 1157672605,
      "purpose": "類似検索・関連リンク用の埋め込みベクトル",
      "dimensions": 1024,
      "notes": "多言語(日本語良好)。索引は次元 1024 で設計すること"
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
{"id": "large", "installed": false,
 "unavailable_reason": "backend model 'qwen3.8-flash-next-iq3_s' is not loaded on the strata runtime"}
```

(Strata を停止して確認した実際の出力。ollama 側の未 pull は `... is not pulled on the ollama runtime` になる。)

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
  "model": "large",
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
2. **thinking はカタログの `thinking` フィールドに従う。**
   - `large`: **既定は思考なし**(ハブが `"reasoning_effort": "none"` を補う)。考えさせたいときだけボディに `"reasoning_effort": "low"` / `"medium"` / `"high"` を入れる。
   - 思考は `message.reasoning_content`、最終回答は `message.content` に入る。
3. **`large` は1リクエストずつ処理する。** 同時要求は順番待ちになる。クライアント側で同時実行数を絞る。
4. **長文入力は読み込みに時間がかかる。** prefill は約 1200〜1700 tok/s。10 万トークンを送ると、最初の1文字まで1分以上かかる。
5. **タイムアウトは 120秒以上(長文なら 600秒)。** gateway 側の `large` のタイムアウトは 900 秒。
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

   # 要約(既定で思考なし。速く返る)
   r = client.chat.completions.create(
       model="large",
       messages=[{"role": "user", "content": "……を3行で要約して"}],
       max_tokens=2048,
   )
   print(r.choices[0].message.content)

   # 難しい問題だけ考えさせる
   r = client.chat.completions.create(
       model="large",
       messages=[{"role": "user", "content": "……の不具合の原因を推理して"}],
       reasoning_effort="medium",
       max_tokens=8192,
   )

   # 埋め込み
   v = client.embeddings.create(model="embed", input="テキスト").data[0].embedding
   assert len(v) == 1024
   ```

### 設計の約束(クライアントが依存してよいこと / いけないこと)

| 依存してよい | 依存してはいけない |
|---|---|
| 論理名 `large` / `embed` | 実体名 `qwen3.8-flash-next-iq3_s` 等(`backend` は参考情報) |
| OpenAI互換のリクエスト/レスポンス形状 | 特定の量子化レベル・速度 |
| 埋め込み次元 1024(変更時は再索引が要る旨を通知) | モデルの内部的な思考トークン数 |

### チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を env 化(鍵をコードに埋めない)
- [ ] HTTPタイムアウトを 120秒以上に(長文入力なら 600秒)
- [ ] 同時リクエスト数を絞る(`large` は1件ずつ処理)
- [ ] 思考を有効にするときは `max_tokens` を十分に取り、`content` が空のケースを扱う
- [ ] 埋め込みは次元 **1024** で索引を設計
- [ ] 会話履歴は自前で保持し毎回送る(ハブは覚えない)

---

## 8. ネットワークとセキュリティ

- **公開範囲:プライベートLAN内のみ。インターネット非公開。**
- **実アクセス制御 = APIキー(LiteLLM master key)**。鍵が無ければ叩けない。
  - カタログAPI も同じ鍵。キー無し → 401、不正キー → 400。catalog サービス自身でも Bearer を検証する(gateway の pass-through 認証に依存しきらない多層防御)。
- 推論 API として LAN に出すのはゲートウェイ(`20800`)。**ollama と catalog は公開しない**。
- Strata(`20803`)は Web UI 用に LAN へ出している。Strata の API キー(`.env` の `STRATA_API_KEY`)が無いと API は 401。gateway の master key とは別の鍵で、gateway → strata の認証にも使う。
- 「LAN内のみ」の担保は運用責任:
  - ルータでポート転送しない / Windows Firewall でWAN遮断。
  - さらに絞るなら `"192.168.x.y:20800:4000"` で特定LAN IP にバインド。
- master key・Strata の API キーは `.env` で管理し、リポジトリにコミットしない(`.gitignore` 済み)。

---

## 9. デプロイ

定義は [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) / [litellm.config.yaml](l-llm-containers/litellm.config.yaml) が正。ここでは要点のみ。

```powershell
cd l-llm-containers
docker compose up -d                       # 全コンテナ起動
```

- **前提(Strata)**
  - `~/.wslconfig` の `[wsl2]` に `memory=72GB` / `swap=8GB`。変えたら `wsl --shutdown` して Docker Desktop を起動し直す(他プロジェクトのコンテナも止まる。`restart: no` のものは手で起こす)。止める前に `docker stop $(docker ps -q)` でコンテナを正常停止させる(強制終了すると postgres が起動時に復旧処理を走らせる)。再起動後にハブのポート(20800〜20803)がホストから応答しないときは `docker compose up -d --force-recreate --no-deps gateway chat openclaw strata` で作り直す(2026-11-13 に発生)。
  - `.env` に `STRATA_API_KEY`(16 文字以上)。
  - NVIDIA ドライバ 580 以上(cuda13 イメージの要件)。
  - 初回起動はモデルのダウンロード(約 85GB)と検査で数十分かかる。`docker logs -f ai-hub-strata` で進み具合が見える。2回目以降の起動は約1分。
- **設定変更後の反映**
  - `litellm.config.yaml` はバインドマウントのため `docker compose up -d` では再読み込みされない。**`docker restart ai-hub-gateway` が必要**。
  - `catalog/server.py` も同様に `docker restart ai-hub-catalog`。
  - `openclaw/openclaw.json` は `docker restart ai-hub-openclaw`。
  - Strata の環境変数(`MODEL` / `CONTEXT` / `STRATA_SET` 等)は `docker compose up -d strata` で作り直すと反映される。モデルと設定はボリュームに残るので再ダウンロードは起きない。
- **自動起動:** Docker Desktop を「ログオン時起動」+ 各サービス `restart: unless-stopped`。
- **GPUパススルー:** Docker Desktop(WSL2)+ NVIDIA Container Toolkit。

---

## 10. 運用

- **ollama のモデル取得は手動**(compose では自動化されない。Strata は初回起動時に自分でダウンロードする):
  ```powershell
  docker exec ai-hub-ollama ollama pull bge-m3        # embed
  # 非公開の生成モデル(戻すとき用に重みだけ保持)
  docker exec ai-hub-ollama ollama pull qwen3:14b     # 旧 quality
  docker exec ai-hub-ollama ollama pull qwen3.5:9b    # 旧 quality-next
  ```
- **Strata の稼働確認**:
  ```powershell
  docker logs --tail 20 ai-hub-strata                  # 起動の進み具合
  docker exec -it ai-hub-strata strata monitor         # ライブモニタ(q で抜ける)
  docker exec ai-hub-strata strata monitor --once      # 1回分のスナップショット
  ```
  ブラウザで `http://192.168.1.111:20803` を開くと Web UI(Chat / Monitor)が使える。API キーは `.env` の `STRATA_API_KEY`。
- **Strata の更新**:`image` のタグ(`0.2.13-cuda13` 等)を書き換えて `docker compose up -d strata`。エンジンはイメージに入っているので、コンテナ内で `strata update` はしない。
- **モデルを追加したら** [litellm.config.yaml](l-llm-containers/litellm.config.yaml) に論理名と `model_info` を書き、gateway を再起動する。**pull せずに書くとカタログには出ないが `/v1/models` には出てしまう**ので、必ず両方揃える。
- **稼働確認**:
  ```powershell
  docker exec ai-hub-ollama ollama list      # 取得済みモデル
  docker exec ai-hub-ollama ollama ps        # ロード状態(CONTEXT列で文脈長)
  nvidia-smi                                 # VRAM
  curl http://localhost:20800/health/liveliness
  curl http://localhost:20800/v1/catalog -H "Authorization: Bearer $KEY"
  ```
- **重みの永続**:ollama は `ollama_models` ボリューム、Strata は `ai-hub-strata-data` ボリューム。**ollama 側は喪失事例あり**(2026-11-13 にも、セッション開始前に `ollama_models` と `openclaw_state` が消えていた)。API不調時はまず `ollama list` を確認し、空なら再 pull。`/v1/catalog?all=1` は喪失を `installed: false` として即座に可視化する。`docker compose down -v` は Strata の約 87GB も消すので使わない。
- **更新**:`docker compose pull && docker compose up -d`。モデル更新は `ollama pull`。

---

## 11. 設計原則

1. **ハブは推論だけを返す。** アプリ固有ロジックは持たない。
2. **ランタイムを直接LANに晒さない。** 必ず認証付きゲートウェイ経由。推論APIの窓口は gateway ただ1つ(Strata の `:20803` は Web UI 用の例外で、API キー必須)。
3. **論理モデル名で隔離。** 実体・量子化の差し替えはゲートウェイ設定に閉じ、クライアントを壊さない。
4. **一覧したものは必ず呼べる。** 設定に書いただけの「呼べないモデル」を外部に見せない(§5)。
5. **常駐は最小限。** VRAM が律速。生成1(Strata)+ 埋め込み1(ollama)を常駐。
6. **鍵で守り、ネットワークで漏らさない。** master key 必須・インターネット非公開。

---

## 12. 今後詰める項目(未確定)

- 旧 `quality` / `quality-next` を利用していたクライアントを `large` に移すこと(論理名が消えたので、カタログに無い id を呼ぶクライアントは 400 になる)。
- `large` の画像入力(Strata は `--vision` で起動している)を gateway 経由で使えるか確認し、使えるなら `model_info` に書く。
- `large` を複数クライアントで同時に使う場合の待ち行列。DFlash2 を切って並列数を上げると生成が遅くなるため、現状は1件ずつ。
- `fast`(軽量・高速)枠を復活させるか。VRAM の空きが約 1.1GB しかないため、置くなら CPU 実行が前提。
- 埋め込みモデルの確定(`bge-m3` vs `multilingual-e5-large`)。次元変更は**クライアント側の再索引**が要るため慎重に。
- ゲートウェイのキー運用(クライアントごとの virtual key を発行するか、master key 単一で済ますか)。
- 監視/ログの保存先と保持期間。
- 同時要求時のキューイング/タイムアウト方針(GPU単一=実質直列)。
