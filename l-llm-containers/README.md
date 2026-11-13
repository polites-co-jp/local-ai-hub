# l-llm-containers

LAN内ローカルLLM推論ハブのコンテナ定義。設計は [../ARCHITECTURE.md](../ARCHITECTURE.md) を参照。

- `docker-compose.yaml` … ollama(埋め込み/非公開) + strata(生成 `large`/`:20803`) + catalog + gateway(LiteLLM/LAN公開 `:20800`) + chat(`:20801`) + openclaw(`:20802`)
- `litellm.config.yaml` … 論理モデル名 `large` / `embed` のルーティング(`quality` / `quality-next` / `fast` / `gemma` はコメントアウト中)
- `.env` … `LITELLM_MASTER_KEY` / `OPENCLAW_GATEWAY_TOKEN` / `STRATA_API_KEY`(コミットしない / `.env.example` をコピーして作成)

## ポート

| サービス | host | container | 公開 |
|---|---|---|---|
| gateway (LiteLLM) | `20800` | `4000` | LAN公開 |
| chat (動作確認UI) | `20801` | `8000` | LAN公開 |
| openclaw (Control UI) | `20802` | `18789` | LAN公開 |
| strata (Web UI / API) | `20803` | `8080` | LAN公開(API キー必須) |
| ollama | ― | `11434` | コンテナ内のみ(非公開) |

> ポート台帳(Notion): local-ai-hub に `20800-20899` を予約済み。写しは [../docs/port-registry.md](../docs/port-registry.md)。

## Strata(生成モデル `large`)

[Strata](https://github.com/Niko1221/Strata) は GPU・RAM・SSD の3層に MoE の専門家を置く推論エンジン。Qwen3.8-Flash-Next IQ3_S(125B / アクティブ 6B)を 128k 文脈で動かし、gateway の論理名 `large` として提供する。

**前提**

- `~/.wslconfig` の `[wsl2]` に `memory=72GB` / `swap=8GB`(旧設定 16GB では起動しない)。変えるときは `docker stop $(docker ps -q)` → `wsl --shutdown` → Docker Desktop を起動し直す。再起動後にハブのポートがホストから応答しなければ `docker compose up -d --force-recreate --no-deps gateway chat openclaw strata`。
- NVIDIA ドライバ 580 以上(`0.2.13-cuda13` イメージの要件)。
- `.env` に `STRATA_API_KEY`(16 文字以上)。

```powershell
docker compose up -d strata
docker logs -f ai-hub-strata        # 初回はダウンロード(約 85GB)と検査で数十分。2回目以降は約1分
```

- 重みはボリューム `ai-hub-strata-data`(Docker のデータ領域 = D: の NVMe)。E: は HDD で速度が出ないため、バインドマウントにはしていない。
- Web UI: ブラウザで `http://192.168.1.111:20803` を開く(API キーは `STRATA_API_KEY`)。
- ライブモニタ: `docker exec -it ai-hub-strata strata monitor`(`q` で抜ける)。
- 設定は環境変数(`MODEL` / `CONTEXT` / `STRATA_SET` 等)。変えたら `docker compose up -d strata` で作り直す。モデルはボリュームに残るので再ダウンロードは起きない。
- `STRATA_SET` の `vram-reserve-mib=1792` は bge-m3 と KV の伸びのぶん VRAM を残す設定。外すと Strata が VRAM を使い切り、長文で数 tok/s に落ちることがある。
- `STRATA_SET` の `STRATA_PLE_AUTO_RAM=0` は n-gram 表(26.8GB)を NVMe から読ませる設定。外すと表がページキャッシュに載り、WSL VM が上限まで膨らむ。
- 停止には最大2分かかる(`stop_grace_period: 2m`)。`docker kill` で止めない。
- 更新: `image` のタグを書き換えて `docker compose up -d strata`。コンテナ内で `strata update` は使わない。

## チャット動作確認アプリ

`chat/` … 全履歴を毎回まるごと gateway へ送る最小チャットUI(`server.py` = Python標準ライブラリのみの薄いプロキシ + `index.html`)。master key はサーバ側のみ保持しブラウザには渡さない。

```powershell
docker compose up -d chat
```

ブラウザで `http://localhost:20801`(LAN内からは `http://<このマシンのLAN IP>:20801`)。
既定モデルは `large`。画面上部でカタログAPIに載っているモデルに切替可。応答はストリーミング表示。

## OpenClaw(AIエージェント + Control UI)

`openclaw/openclaw.json` … OpenClaw の設定(JSON5)。推論プロバイダ `aihub` として gateway(`http://gateway:4000/v1`)を登録し、既定モデルを `aihub/large`、思考の既定を `off` にしている。
セッション・記憶・ワークスペースはボリューム `openclaw_state` に残る。設定を変えたら `docker restart ai-hub-openclaw`。

```powershell
docker compose up -d openclaw
```

1. ブラウザで `http://localhost:20802`(LAN内からは `http://192.168.1.111:20802`)を開く。
2. `.env` の `OPENCLAW_GATEWAY_TOKEN` を入力して接続する。
3. `pairing required` と出たら、ホストで承認する(ブラウザごとに初回のみ。`openclaw_state` を作り直したときもやり直し):

```powershell
docker exec ai-hub-openclaw node dist/index.js devices list
docker exec ai-hub-openclaw node dist/index.js devices approve <requestId>
```

- 開けるのは `gateway.controlUi.allowedOrigins` に書いた Origin だけ。別のホスト名や IP で開くならそこへ追加する。
- CLI からエージェントを動かす: `docker exec ai-hub-openclaw node dist/index.js agent -m "こんにちは"`
- `large` は1リクエストずつ処理するため、並列のサブエージェントは順番待ちになる。
- バージョンは compose で固定している。上げるときは `image` のタグを書き換えて `docker compose up -d openclaw`。

## 初回セットアップ

```powershell
# 0) GPU パススルー疎通確認(初回のみ)
docker run --rm --gpus all ollama/ollama

# 1) .env 作成(鍵を差し替え)
cp .env.example .env   # PowerShell: Copy-Item .env.example .env

# 2) スタック起動(strata は初回にモデルをダウンロードする)
docker compose up -d

# 3) ollama のモデル取得(compose では自動化されない)
docker exec ai-hub-ollama ollama pull bge-m3         # embed
```

## 動作確認

```powershell
# ロード状態(ollama は bge-m3 だけが常駐していればよい)
docker exec ai-hub-ollama ollama ps
docker exec ai-hub-strata strata monitor --once

# ゲートウェイ疎通(LAN IP は自マシンのアドレスに置換)
curl http://localhost:20800/health/liveliness

# 生成
curl http://localhost:20800/v1/chat/completions `
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" `
  -H "Content-Type: application/json" `
  -d '{"model":"large","messages":[{"role":"user","content":"3行で自己紹介して"}]}'

# 埋め込み
curl http://localhost:20800/v1/embeddings `
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" `
  -H "Content-Type: application/json" `
  -d '{"model":"embed","input":"テスト文"}'
```

## 運用

- 更新: `docker compose pull; docker compose up -d` / ollama のモデル更新は `ollama pull`
- 自動起動: Docker Desktop「ログオン時起動」+ 各サービス `restart: unless-stopped`
- 重みの永続: `ollama_models` ボリューム(喪失時は再 pull で復旧)と `ai-hub-strata-data` ボリューム。`docker compose down -v` は Strata の約 87GB も消すので使わない

## 非公開にした生成モデルを戻す

`quality`(`qwen3:14b`)と `quality-next`(`qwen3.5:9b`)の重みは ollama に残してあり、`litellm.config.yaml` でコメントアウトしている。
`large` と同時に GPU に載せると VRAM が足りないので、戻すときは Strata を止める(`docker stop ai-hub-strata`)か、VRAM の予算を測り直してからコメントを外し、`docker restart ai-hub-gateway` する。

## クライアント統合

```
AI_HUB_URL=http://<このマシンのLAN IP>:20800
AI_HUB_KEY=<LITELLM_MASTER_KEY>
```

OpenAI 互換 SDK で `model: "large"` / `"embed"` を叩く。
