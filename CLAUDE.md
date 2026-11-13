# CLAUDE.md

このリポジトリで作業する Claude Code 向けのガイド。詳細設計は [ARCHITECTURE.md](ARCHITECTURE.md)、外部クライアント連携は [ai-hub-client-guide.md](ai-hub-client-guide.md) を参照。

## プロジェクト概要

**ai-hub** = LAN内の汎用ローカルLLM推論ハブ。GPU搭載マシン上で、**OpenAI互換APIのエンドポイント1つ**の裏にローカルLLM(生成)と埋め込みモデルを束ねて提供する共有サービス。

- クライアントは**論理モデル名**(`large` / `embed`)だけを指定。実体(`qwen3.8-flash-next-iq3_s` 等)はハブ側で隠蔽し、差し替えてもクライアントは無変更。
- **ステートレス**。会話履歴はクライアントが保持し毎回まるごと送る。サーバに記憶はない。
- 公開範囲は**プライベートLAN内のみ**。インターネット非公開。アクセス制御はAPIキー(LiteLLM master key)。
- アプリ固有ロジック(取り込み・推薦等)はクライアント側の責務。ハブは「推論を返す」だけ。

## 構成（6コンテナ）

すべて [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) で定義。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 埋め込みランタイム。bge-m3 だけを常駐 | `11434`(内部のみ) | 非公開 |
| `ai-hub-strata` | 生成ランタイム [Strata](https://github.com/Niko1221/Strata)。Qwen3.8-Flash-Next IQ3_S(125B MoE / アクティブ 6B)を論理名 `large` で提供。Web UI あり | host `20803`→`8080` | LAN公開(API キー必須) |
| `ai-hub-catalog` | モデルカタログAPI。実際に呼べるモデルだけを返す(Python標準ライブラリのみ) | `8080`(内部のみ) | 非公開(gateway経由) |
| `ai-hub-gateway` | LiteLLM Proxy。推論APIの窓口。認証/論理名ルーティング/ログ | host `20800`→`4000` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI(Python標準ライブラリのみ)。既定モデルは `large` | host `20801`→`8000` | LAN公開 |
| `ai-hub-openclaw` | OpenClaw(AIエージェント)と管理画面 Control UI。推論は gateway 経由で `large` を使うクライアント | host `20802`→`18789` | LAN公開(トークン+ペアリング) |

データフロー: `クライアント → gateway(:20800, APIキー認証) → strata(large) / ollama(embed) → 応答`。ollama は直接公開しない（必ずゲートウェイ経由）。Strata の `:20803` は Web UI 用に LAN に出しているが、クライアントアプリは gateway の `large` を使う。カタログAPIは gateway の pass-through で `/v1/catalog` として公開する。OpenClaw もハブの利用者の1つで、`http://gateway:4000/v1` を呼ぶ。ハブ自体はステートレスのままで、会話や記憶を持つのは OpenClaw 側。

実行環境: Windows + Ryzen 9 7950X + RAM 128GB + NVIDIA RTX 5060 Ti 16GB / Docker Desktop(WSL2) + NVIDIA Container Toolkit。

## ディレクトリ構成

```
local-ai-hub/
├── CLAUDE.md                  # このファイル
├── ARCHITECTURE.md            # 設計 & 連携手順書(構成/VRAM配分/カタログAPI/接続手順)
├── ai-hub-client-guide.md     # 外部クライアント実装者向けの連携手順書(サンプル/エラー早見表)
├── docs/port-registry.md      # ポート台帳の写し(正本は Notion)
└── l-llm-containers/          # コンテナ定義一式
    ├── docker-compose.yaml    # 6コンテナ定義 + ollama / strata の環境変数
    ├── litellm.config.yaml    # 論理モデル名 → 実体(strata / ollama)のルーティング + model_info(カタログの情報源)
    ├── .env                   # LITELLM_MASTER_KEY / OPENCLAW_GATEWAY_TOKEN / STRATA_API_KEY(コミット禁止 / .gitignore済み)
    ├── .env.example           # .envのテンプレート
    ├── README.md              # セットアップ/運用手順
    ├── catalog/               # カタログAPI(server.py = 実行可能モデルの突合)
    ├── chat/                  # 動作確認UI(server.py = 薄いプロキシ, index.html)
    └── openclaw/              # OpenClaw 設定(openclaw.json = JSON5。状態はボリューム openclaw_state)
```

## よく使うコマンド

> **環境メモ**: シェルは PowerShell。`git` は PATH に無く SourceTree 同梱版
> `C:\Users\kou_z\AppData\Local\Atlassian\SourceTree\git_local\cmd\git.exe` を使う。
> 初回は `git config --global --add safe.directory E:/develop/local-ai-hub` が必要。

```powershell
# スタック起動 / 個別起動
docker compose up -d                       # l-llm-containers ディレクトリで
docker compose up -d strata                # Strata の環境変数を変えたら作り直す(モデルはボリュームに残る)

# モデル取得（ollama 側は compose では自動化されない。要手動 pull。Strata は初回起動時に自分で取る）
docker exec ai-hub-ollama ollama pull bge-m3      # embed

# 稼働確認
docker exec ai-hub-ollama ollama ps        # ロード状態(bge-m3 だけが載っていればよい)
docker exec ai-hub-strata strata monitor --once   # Strata の状態・速度・VRAM
docker logs --tail 20 ai-hub-strata        # Strata の起動の進み具合
nvidia-smi                                 # VRAM使用量
curl http://localhost:20800/health/liveliness   # ゲートウェイ疎通(キー不要)
```

## 運用上の重要事項（ハマりどころ）

- **Strata は WSL のメモリを 104GB に上げてある前提**。`~/.wslconfig` の `[wsl2]` が `memory=104GB` / `swap=8GB`(2026-11-13 に 16GB/4GB から変更。バックアップ `~/.wslconfig.bak-20261113`)。Strata は約 55GB の RAM を使い、コンテナにメモリ上限を付けてはいけない。`wsl --shutdown` は他プロジェクトのコンテナも止める。`restart: no` のもの(chiten の大半)は手で `docker start` する。そのとき chiten-api は postgres より先に起きるとマイグレーションに失敗するので、postgres が healthy になってから起こす。
- **Strata の重みは名前付きボリューム `ai-hub-strata-data`(約 87GB)**。Docker のデータ領域(D: の NVMe)に置く。E: は HDD で Strata の SSD ストリーミングに使えないため、プロジェクト内バインドマウントのルールの例外にしている。`docker compose down -v` は使わない(再ダウンロードに数十分かかる)。
- **Strata は空いている VRAM を専門家キャッシュで使い切る**。`STRATA_SET=vram-reserve-mib=1792` で bge-m3 と KV の伸びのぶんを残している。外すと長文で GPU メモリが RAM に追い出され、数 tok/s に落ちる(エンジンログ `/data/state/strata/launch/docker.engine.log` に `only N MiB of VRAM free` の警告が出る)。
- **VRAM予算(16GB / 16311 MiB)**: 実測(nvidia-smi, デスクトップアプリ込みの総使用量)は以下。
  - 現行(Strata 専門家キャッシュ 5.14GiB + 予備枠 + bge-m3 0.7GB) = **14095〜14915 MiB / 16311 MiB**(2026-11-13 実測)。Strata の読み込み直後の空きは約 1.1GB。
  - **ollama に生成モデルを載せないこと**。quality-next(64k, 7.3GB)を常駐させたままだと Strata のキャッシュは 3.74GiB に縮み、quality(11GB)は入らない。
- **`large` の思考は既定で無効**。Strata 自体の既定は high だが、[litellm.config.yaml](l-llm-containers/litellm.config.yaml) の `litellm_params.reasoning_effort: none` で補っている。クライアントがリクエストに `reasoning_effort` を書けばそちらが優先される。`drop_params: true` に落とされないよう `allowed_openai_params: ["reasoning_effort"]` も必要。
- **`large` は1リクエストずつ処理する**(推測デコードの DFlash2 が1件ずつしか扱わないため)。同時要求は順番待ちになる。長文の入力は prefill が約 1200〜1700 tok/s なので、最初の1文字が出るまで時間がかかる。
- **`quality` / `quality-next` は 2026-11-13 に非公開にした**。重み(`qwen3:14b` / `qwen3.5:9b`)は ollama に残し、[litellm.config.yaml](l-llm-containers/litellm.config.yaml) でコメントアウトしている。戻すなら Strata を止めるか、VRAM 予算を測り直す。
- **ollama のモデルは手動 pull が必要**。`docker compose up` ではモデルは取得されない。ボリューム `l-llm-containers_ollama_models` が空（`ollama list` が空）なら、上記 pull で復旧する。ボリューム内容が消える事例が繰り返しある(2026-11-13 にもセッション開始前に `ollama_models` と `openclaw_state` が消えていた)ため、API不調時はまず `ollama list` を確認。
- **ollama のコンテキスト長設定**(`OLLAMA_CONTEXT_LENGTH=32768`、`OLLAMA_KV_CACHE_TYPE=q8_0`、`OLLAMA_FLASH_ATTENTION=1`)は、非公開の生成モデルを戻すとき用に残してある。
- **master key は [.env](l-llm-containers/.env) のみ**(コミット禁止)。値は `Select-String -Path l-llm-containers/.env -Pattern 'LITELLM_MASTER_KEY=(.+)'` で取得可。Strata の鍵は同じファイルの `STRATA_API_KEY`。
- **論理モデルの追加/差し替えは [litellm.config.yaml](l-llm-containers/litellm.config.yaml)** で行う。クライアントは論理名のみ参照するため実体差し替えの影響を受けない。追加時は `model_info`(用途・thinking の扱い・次元など)も書く。**カタログAPIが返すクライアント向け説明はここが単一の情報源**。
- **`/v1/models` は嘘をつく**。設定に書いた論理名を返すだけで実体の状態を見ないため、未 pull・未起動のモデルも一覧に出て呼ぶと失敗する。**実際に呼べるモデルは `/v1/catalog`**(catalog コンテナが ollama の `/api/tags` と strata の `/v1/models` を突合)。未 pull の論理名は設定でコメントアウトしておく。
- **設定ファイルはバインドマウントなので `docker compose up -d` では再読み込みされない**。`litellm.config.yaml` を変えたら `docker restart ai-hub-gateway`、`catalog/server.py` を変えたら `docker restart ai-hub-catalog`、`openclaw/openclaw.json` を変えたら `docker restart ai-hub-openclaw`。
- **OpenClaw の Control UI(`:20802`)は初回にトークンとブラウザごとのペアリング承認が要る**。トークンは [.env](l-llm-containers/.env) の `OPENCLAW_GATEWAY_TOKEN`。loopback からの接続は自動承認されるが、Docker のポート転送を経由すると localhost から開いても loopback と見なされず、承認待ちになることがある。`docker exec ai-hub-openclaw node dist/index.js devices list` で requestId を見て `devices approve <requestId>` で承認する。
- **OpenClaw の設定は [openclaw/openclaw.json](l-llm-containers/openclaw/openclaw.json)**(バインドマウント)。Control UI から設定を変えるとこのファイルが書き換わる。秘密は `${ENV}` 参照にして直接書かない。思考の既定は `off`(`reasoning_effort: "none"` に変換される)。OpenClaw のエージェントはコンテナ内でシェルを実行でき、環境変数の `LITELLM_MASTER_KEY` も読める。
- **litellm.config.yaml の YAML に注意**。値に `": "` を含む文字列(例: `"reasoning_effort": "low"` という説明文)はクォートしないと `ScannerError` で gateway が起動しなくなる。
