# local-ai-hub

LAN内の汎用ローカルLLM推論ハブ。GPU搭載マシン上で、**OpenAI互換APIのエンドポイント1つ**の裏にローカルLLM(生成)と埋め込みモデルを束ねて提供する共有サービスです。

- クライアントは**論理モデル名**(`large` / `embed`)だけを指定。実体(`qwen3.8-flash-next-iq3_s` 等)はハブ側で隠蔽し、差し替えてもクライアントは無変更。
- 生成の `large` は **Qwen3.8-Flash-Next(125B MoE / アクティブ 6B)**。推論エンジン [Strata](https://github.com/Niko1221/Strata) が GPU・RAM・SSD の3層に専門家を置くので、16GB の GPU + 128GB の RAM で 50〜78 tok/s が出ます。
- **ステートレス**。会話履歴はクライアントが保持し、毎回まるごと送る。サーバに記憶はない。
- 公開範囲は**プライベートLAN内のみ**(インターネット非公開)。アクセス制御はAPIキー(LiteLLM master key)。
- アプリ固有ロジック(取り込み・推薦等)はクライアント側の責務。ハブは「推論を返す」だけ。

## 構成

```
[LAN内クライアント] ──▶ gateway(LiteLLM :20800, APIキー認証) ─┬─▶ strata(large, GPU+RAM+NVMe)
                              │                                └─▶ ollama(embed, GPU, 内部のみ)
                              └─ /v1/catalog ──▶ catalog(実際に呼べるモデルを返す)
```

6コンテナ構成(定義は [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml))。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 埋め込みランタイム(bge-m3 を常駐) | `11434`(内部のみ) | 非公開 |
| `ai-hub-strata` | 生成ランタイム(Strata。論理名 `large`)と Web UI | host `20803` | LAN公開(API キー必須) |
| `ai-hub-catalog` | モデルカタログAPI。実際に呼べるモデルだけを返す | `8080`(内部のみ) | 非公開(gateway経由) |
| `ai-hub-gateway` | LiteLLM Proxy。推論APIの窓口。認証/論理名ルーティング/ログ | host `20800` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI(既定モデル `large`) | host `20801` | LAN公開 |
| `ai-hub-openclaw` | OpenClaw(AIエージェント)と管理画面 Control UI。gateway 経由で `large` を使う | host `20802` | LAN公開 |

実行環境: Windows 11 + Ryzen 9 7950X + RAM 128GB + NVIDIA RTX 5060 Ti 16GB / Docker Desktop(WSL2)+ NVIDIA Container Toolkit。

## クイックスタート

```powershell
# 前提: ~/.wslconfig の [wsl2] に memory=72GB を設定し、wsl --shutdown 済み
cd l-llm-containers
Copy-Item .env.example .env    # LITELLM_MASTER_KEY / OPENCLAW_GATEWAY_TOKEN / STRATA_API_KEY を設定
docker compose up -d           # 初回は Strata がモデル(約 85GB)をダウンロードする

# 埋め込みモデル取得(ollama 側は compose では自動化されない。手動 pull が必要)
docker exec ai-hub-ollama ollama pull bge-m3      # embed

# 疎通確認
curl http://localhost:20800/health/liveliness
```

詳細な手順は [l-llm-containers/README.md](l-llm-containers/README.md) を参照。

## 使い方(クライアント側)

```python
from openai import OpenAI
client = OpenAI(base_url="http://<ハブのLAN IP>:20800/v1", api_key="<LITELLM_MASTER_KEY>")

# 生成(既定で思考しない)
r = client.chat.completions.create(
    model="large",
    messages=[{"role": "user", "content": "……を3行で要約して"}],
    max_tokens=2048,
)
print(r.choices[0].message.content)

# 埋め込み(1024次元)
v = client.embeddings.create(model="embed", input="テキスト").data[0].embedding
```

> **注意**: `large` は既定で思考しません。考えさせたいときだけ `reasoning_effort="low" / "medium" / "high"` を付けます。処理は1リクエストずつで、長文の入力は読み込みに時間がかかります。`/v1/models` は実体の状態を見ないため、**実際に呼べるモデルの一覧は `/v1/catalog`** を使います。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 設計 & 連携手順書(構成 / VRAM配分 / カタログAPI / 接続手順) |
| [ai-hub-client-guide.md](ai-hub-client-guide.md) | 外部クライアント実装者向けの連携手順書(サンプルコード / エラー早見表) |
| [l-llm-containers/README.md](l-llm-containers/README.md) | コンテナのセットアップ / 運用手順 |
| [CLAUDE.md](CLAUDE.md) | Claude Code 向けガイド(運用上のハマりどころ含む) |

## セキュリティ

- ollama は直接LANに公開しない。推論APIの窓口は gateway(`:20800`)。
- Strata の Web UI(`:20803`)は LAN に出しているが、API キー(`STRATA_API_KEY`)が無いと API は 401。
- 鍵は `l-llm-containers/.env` のみで管理(`.gitignore` 済み。コミット禁止)。
- ルータでポート転送しない / ファイアウォールでWANを遮断し、「LAN内のみ」を担保する。
