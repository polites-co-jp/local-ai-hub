# local-ai-hub

LAN内の汎用ローカルLLM推論ハブ。GPU搭載マシン上で、**OpenAI互換APIのエンドポイント1つ**の裏にローカルLLM(生成)と埋め込みモデルを束ねて提供する共有サービスです。

- クライアントは**論理モデル名**(`quality` / `quality-next` / `embed`)だけを指定。実体(`qwen3:14b` 等)はハブ側で隠蔽し、差し替えてもクライアントは無変更。
- **ステートレス**。会話履歴はクライアントが保持し、毎回まるごと送る。サーバに記憶はない。
- 公開範囲は**プライベートLAN内のみ**(インターネット非公開)。アクセス制御はAPIキー(LiteLLM master key)。
- アプリ固有ロジック(取り込み・推薦等)はクライアント側の責務。ハブは「推論を返す」だけ。

## 構成

```
[LAN内クライアント] ──▶ gateway(LiteLLM :20800, APIキー認証) ──▶ ollama(GPU, 内部のみ)
                              │
                              └─ /v1/catalog ──▶ catalog(実際に呼べるモデルを返す)
```

4コンテナ構成(定義は [l-llm-containers/docker-compose.yaml](l-llm-containers/docker-compose.yaml))。

| コンテナ | 役割 | ポート | 公開 |
|---|---|---|---|
| `ai-hub-ollama` | 推論ランタイム(GPU占有)。OpenAI互換 | `11434`(内部のみ) | 非公開 |
| `ai-hub-catalog` | モデルカタログAPI。実際に呼べるモデルだけを返す | `8080`(内部のみ) | 非公開(gateway経由) |
| `ai-hub-gateway` | LiteLLM Proxy。唯一の外部窓口。認証/論理名ルーティング/ログ | host `20800` | LAN公開 |
| `ai-hub-chat` | 動作確認用の薄いチャットUI | host `20801` | LAN公開 |

実行環境: Windows 11 + NVIDIA RTX 5060 Ti 16GB / Docker Desktop(WSL2)+ NVIDIA Container Toolkit。

## クイックスタート

```powershell
cd l-llm-containers
Copy-Item .env.example .env    # LITELLM_MASTER_KEY を設定
docker compose up -d

# モデル取得(compose では自動化されない。手動 pull が必要)
docker exec ai-hub-ollama ollama pull qwen3:14b   # quality
docker exec ai-hub-ollama ollama pull bge-m3      # embed

# 疎通確認
curl http://localhost:20800/health/liveliness
```

詳細な手順は [l-llm-containers/README.md](l-llm-containers/README.md) を参照。

## 使い方(クライアント側)

```python
from openai import OpenAI
client = OpenAI(base_url="http://<ハブのLAN IP>:20800/v1", api_key="<LITELLM_MASTER_KEY>")

# 生成
r = client.chat.completions.create(
    model="quality",
    messages=[{"role": "user", "content": "……を3行で要約して"}],
    max_tokens=2048,
)
print(r.choices[0].message.content)

# 埋め込み(1024次元)
v = client.embeddings.create(model="embed", input="テキスト").data[0].embedding
```

> **注意**: `quality` 等は思考(thinking)モデルです。思考の止め方や `max_tokens` の注意点はモデルごとに異なるため、`GET /v1/catalog` が返す `thinking` フィールドに従ってください。`/v1/models` は未 pull のモデルも返すため、**実際に呼べるモデルの一覧は `/v1/catalog`** を使います。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 設計 & 連携手順書(構成 / VRAM配分 / カタログAPI / 接続手順) |
| [ai-hub-client-guide.md](ai-hub-client-guide.md) | 外部クライアント実装者向けの連携手順書(サンプルコード / エラー早見表) |
| [l-llm-containers/README.md](l-llm-containers/README.md) | コンテナのセットアップ / 運用手順 |
| [CLAUDE.md](CLAUDE.md) | Claude Code 向けガイド(運用上のハマりどころ含む) |

## セキュリティ

- ollama は直接LANに公開しない。外部窓口は gateway(`:20800`)ただ1つ。
- master key は `l-llm-containers/.env` のみで管理(`.gitignore` 済み。コミット禁止)。
- ルータでポート転送しない / ファイアウォールでWANを遮断し、「LAN内のみ」を担保する。
