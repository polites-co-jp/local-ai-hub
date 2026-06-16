# CLAUDE.md

このリポジトリで作業する Claude Code 向けのガイド。詳細設計は [ARCHITECTURE.md](ARCHITECTURE.md)、外部クライアント連携は [ai-hub-client-guide.md](ai-hub-client-guide.md) を参照。

## プロジェクト概要

**ai-hub** = LAN内の汎用ローカルLLM推論ハブ。GPU搭載マシン上で、**OpenAI互換APIのエンドポイント1つ**の裏にローカルLLM(生成)と埋め込みモデルを束ねて提供する共有サービス。

- クライアントは**論理モデル名**(`quality` / `fast` / `embed` / `gemma`)だけを指定。実体(`qwen3:14b` 等)はハブ側で隠蔽し、差し替えてもクライアントは無変更。
- **ステートレス**。会話履歴はクライアントが保持し毎回まるごと送る。サーバに記憶はない。
- 公開範囲は**プライベートLAN内のみ**。インターネット非公開。アクセス制御はAPIキー(LiteLLM master key)。
- アプリ固有ロジック(取り込み・推薦等)はクライアント側の責務。ハブは「推論を返す」だけ。

## 構成（3コンテナ）

すべて [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml) で定義。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 推論ランタイム(GPU占有)。OpenAI互換 | `11434`(内部のみ) | 非公開 |
| `ai-hub-gateway` | LiteLLM Proxy。唯一の外部窓口。認証/論理名ルーティング/ログ | host `20800`→`4000` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI(Python標準ライブラリのみ) | host `20801`→`8000` | LAN公開 |

データフロー: `クライアント → gateway(:20800, APIキー認証) → ollama(:11434) → 応答`。ollama は直接公開しない（必ずゲートウェイ経由）。

実行環境: Windows + NVIDIA RTX 5060 Ti 16GB / Docker Desktop(WSL2) + NVIDIA Container Toolkit。

## ディレクトリ構成

```
local-ai-hub/
├── CLAUDE.md                  # このファイル
├── ARCHITECTURE.md            # 設計メモ(目的/構成/VRAM配分/セキュリティ)
├── ai-hub-client-guide.md     # 外部クライアント実装者向けの連携手順書
└── l-llm-containers/          # コンテナ定義一式
    ├── docker-compose.yaml    # 3コンテナ定義 + ollama環境変数(コンテキスト長等)
    ├── litellm.config.yaml    # 論理モデル名 → ollama実体のルーティング
    ├── .env                   # LITELLM_MASTER_KEY(コミット禁止 / .gitignore済み)
    ├── .env.example           # .envのテンプレート
    ├── README.md              # セットアップ/運用手順
    └── chat/                  # 動作確認UI(server.py = 薄いプロキシ, index.html)
```

## よく使うコマンド

> **環境メモ**: シェルは PowerShell。`git` は PATH に無く SourceTree 同梱版
> `C:\Users\kou_z\AppData\Local\Atlassian\SourceTree\git_local\cmd\git.exe` を使う。
> 初回は `git config --global --add safe.directory E:/develop/local-ai-hub` が必要。

```powershell
# スタック起動 / 個別起動
docker compose up -d                       # l-llm-containers ディレクトリで
docker compose up -d ollama                # 設定変更後の再作成(env反映)

# モデル取得（compose では自動化されない。要手動 pull）
docker exec ai-hub-ollama ollama pull qwen3:14b   # quality
docker exec ai-hub-ollama ollama pull bge-m3      # embed
docker exec ai-hub-ollama ollama pull llama3.1:8b # fast(オンデマンド)

# 稼働確認
docker exec ai-hub-ollama ollama list      # 取得済みモデル
docker exec ai-hub-ollama ollama ps        # ロード状態(CONTEXT列でコンテキスト長確認)
nvidia-smi                                 # VRAM使用量
curl http://localhost:20800/health/liveliness   # ゲートウェイ疎通(キー不要)
```

## 運用上の重要事項（ハマりどころ）

- **モデルは手動 pull が必要**。`docker compose up` ではモデルは取得されない。ボリューム `l-llm-containers_ollama_models` が空（`ollama list` が空）なら、上記 pull で復旧する。コンテナ再作成でボリューム内容が消える事例があったため、API不調時はまず `ollama list` を確認。
- **コンテキスト長は [docker-compose.yaml](l-llm-containers/docker-compose.yaml) の ollama 環境変数で制御**:
  - `OLLAMA_CONTEXT_LENGTH=32768` … 受付窓(入力+思考+出力の合計)。既定4096では Qwen3(thinking)が途中打ち切りになるため引き上げ済み。32768 は Qwen3 ネイティブ上限。
  - `OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1` … KVキャッシュを量子化しVRAM半減。これが無いと16GBに32k文脈が収まらない。
- **VRAM予算(16GB)**: qwen3:14b(Q4_K_M, 32k文脈) ~13GB + bge-m3 ~0.7GB ≈ 13.6GB。両モデル常駐で100% GPU(CPUオフロード無し)に収まる。モデル追加時はこの予算を超えないこと。常駐は最小限(生成1+埋め込み1)、追加はオンデマンド。
- **`quality`(Qwen3 14B)は推論モデル**。応答に思考過程が含まれ `message.reasoning_content` に入る。最終回答は `message.content`。`max_tokens` が小さいと思考の途中で打ち切られ `content` が空になる。
- **master key は [.env](l-llm-containers/.env) のみ**(コミット禁止)。値は `Select-String -Path l-llm-containers/.env -Pattern 'LITELLM_MASTER_KEY=(.+)'` で取得可。
- **論理モデルの追加/差し替えは [litellm.config.yaml](l-llm-containers/litellm.config.yaml)** で行う。クライアントは論理名のみ参照するため実体差し替えの影響を受けない。
