# l-llm-containers

LAN内ローカルLLM推論ハブのコンテナ定義。設計は [../ARCHITECTURE.md](../ARCHITECTURE.md) を参照。

- `docker-compose.yaml` … ollama(GPU/非公開) + gateway(LiteLLM/LAN公開 `:20800`)
- `litellm.config.yaml` … 論理モデル名 `quality` / `embed` / `fast` のルーティング
- `.env` … `LITELLM_MASTER_KEY`(コミットしない / `.env.example` をコピーして作成)

## ポート

| サービス | host | container | 公開 |
|---|---|---|---|
| gateway (LiteLLM) | `20800` | `4000` | LAN公開 |
| chat (動作確認UI) | `20801` | `8000` | LAN公開 |
| ollama | ― | `11434` | コンテナ内のみ(非公開) |

> ポート台帳(Notion): local-ai-hub に `20800-20899` を予約済み。

## チャット動作確認アプリ

`chat/` … 全履歴を毎回まるごと gateway へ送る最小チャットUI(`server.py` = Python標準ライブラリのみの薄いプロキシ + `index.html`)。master key はサーバ側のみ保持しブラウザには渡さない。

```powershell
docker compose up -d chat
```

ブラウザで `http://localhost:20801`(LAN内からは `http://<このマシンのLAN IP>:20801`)。
モデルは画面上部で `quality` / `fast` を切替可。応答はストリーミング表示(GPU未使用時は低速)。

## 初回セットアップ

```powershell
# 0) GPU パススルー疎通確認(初回のみ)
docker run --rm --gpus all ollama/ollama

# 1) .env 作成(鍵を差し替え)
cp .env.example .env   # PowerShell: Copy-Item .env.example .env

# 2) スタック起動
docker compose up -d

# 3) モデル取得(compose では自動化されない)
docker exec ai-hub-ollama ollama pull qwen3:14b      # quality (Q4_K_M, 既定)
docker exec ai-hub-ollama ollama pull bge-m3         # embed
docker exec ai-hub-ollama ollama pull llama3.1:8b    # fast (オンデマンド)
```

## 動作確認

```powershell
# 常駐確認(loaded のまま = keep-alive 有効)
docker exec ai-hub-ollama ollama ps

# ゲートウェイ疎通(LAN IP は自マシンのアドレスに置換)
curl http://localhost:20800/health/liveliness

# 生成
curl http://localhost:20800/v1/chat/completions `
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" `
  -H "Content-Type: application/json" `
  -d '{"model":"quality","messages":[{"role":"user","content":"3行で自己紹介して"}]}'

# 埋め込み
curl http://localhost:20800/v1/embeddings `
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" `
  -H "Content-Type: application/json" `
  -d '{"model":"embed","input":"テスト文"}'
```

## 運用

- 更新: `docker compose pull; docker compose up -d` / モデル更新は `ollama pull`
- 自動起動: Docker Desktop「ログオン時起動」+ 各サービス `restart: unless-stopped`
- 重みの永続: `ollama_models` ボリューム(喪失時は再 pull で復旧)

## 品質ノブ(Q5_K_M 等へ差し替え)

ollama 公式の `qwen3:14b` は Q4_K_M / q8_0 / fp16 のみ(Q5_K_M タグなし)。
Q5_K_M を使うなら HuggingFace GGUF 経由に差し替え、`litellm.config.yaml` の `quality` を変更:

```yaml
  - model_name: quality
    litellm_params:
      model: ollama_chat/hf.co/unsloth/Qwen3-14B-GGUF:Q5_K_M
      api_base: http://ollama:11434
```

クライアントは論理名 `quality` のみ参照するため、実体差し替えで影響を受けない。

## クライアント統合

```
AI_HUB_URL=http://<このマシンのLAN IP>:20800
AI_HUB_KEY=<LITELLM_MASTER_KEY>
```

OpenAI 互換 SDK で `model: "quality"` / `"embed"` / `"fast"` を叩く。
