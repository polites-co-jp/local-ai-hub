# ai-hub API リファレンス

LAN 内のローカル LLM 推論ハブ **ai-hub** を、外部アプリケーションから使うためのリファレンスです。
このファイルだけで接続・実装できるように書いてあります。応答例はすべて実機から採取したものです(2026-11-13)。

---

## 目次

1. [概要](#1-概要)
2. [接続情報と認証](#2-接続情報と認証)
3. [エンドポイント一覧](#3-エンドポイント一覧)
4. [モデル](#4-モデル)
5. [モデルカタログ `GET /v1/catalog`](#5-モデルカタログ-get-v1catalog)
6. [生成 `POST /v1/chat/completions`](#6-生成-post-v1chatcompletions)
7. [埋め込み `POST /v1/embeddings`](#7-埋め込み-post-v1embeddings)
8. [Strata ネイティブ API `/strata/*`](#8-strata-ネイティブ-api-strata)
9. [エラー](#9-エラー)
10. [制約と性能](#10-制約と性能)
11. [実装チェックリスト](#11-実装チェックリスト)

---

## 1. 概要

- **OpenAI 互換 API** のエンドポイント1つの裏に、生成モデルと埋め込みモデルがまとまっています。OpenAI の SDK がそのまま使えます。
- 生成は Anthropic Messages 形式・OpenAI Responses 形式でも呼べます(§8)。
- クライアントは **論理モデル名**(`large` / `embed`)だけを指定します。実体のモデルはハブ側で差し替わることがありますが、論理名は変わりません。
- **ステートレス**です。サーバは会話を覚えません。会話履歴はクライアントが持ち、毎回 `messages` に全部入れて送ります。
- **LAN 内専用**です。インターネットからは到達できません。

## 2. 接続情報と認証

| 項目 | 値 |
|---|---|
| ベース URL(LAN 内の別マシンから) | `http://192.168.1.111:20800` |
| ベース URL(ハブと同じマシンから) | `http://localhost:20800` |
| OpenAI SDK の `base_url` | `http://192.168.1.111:20800/v1` |
| Anthropic SDK の `base_url` | `http://192.168.1.111:20800/strata` |
| 認証 | `Authorization: Bearer <API キー>`(`/strata/*` では `x-api-key: <API キー>` も可) |
| 通信 | HTTP(TLS なし・LAN 内のみ) |

API キーはハブ管理者から受け取ってください。コードに直接書かず、環境変数で持ちます。

```dotenv
AI_HUB_URL=http://192.168.1.111:20800
AI_HUB_KEY=sk-xxxx          # .env は .gitignore に入れる
```

## 3. エンドポイント一覧

| メソッド | パス | 用途 | 認証 |
|---|---|---|---|
| GET | `/health/liveliness` | 疎通確認(`"I'm alive!"` を返す) | 不要 |
| GET | `/v1/catalog` | **今すぐ呼べるモデルの一覧と、その使い方**(推奨) | 必要 |
| GET | `/v1/models` | OpenAI 標準のモデル一覧。実体の状態を見ないので、**判断には `/v1/catalog` を使う** | 必要 |
| POST | `/v1/chat/completions` | 生成(OpenAI Chat Completions)。ストリーム・tools・画像対応 | 必要 |
| POST | `/v1/embeddings` | 埋め込みベクトル | 必要 |
| POST | `/strata/v1/messages` | 生成(Anthropic Messages 形式) | 必要 |
| POST | `/strata/v1/messages/count_tokens` | トークン数の計算(Anthropic 形式) | 必要 |
| POST | `/strata/v1/responses` | 生成(OpenAI Responses 形式・ステートレス) | 必要 |
| POST | `/strata/v1/chat/completions` | 生成(Chat Completions を生成エンジンへ直接) | 必要 |
| GET | `/strata/v1/models` | 生成エンジンに読み込まれているモデル | 必要 |
| GET | `/strata/status` | 生成エンジンが処理中か・待ち行列の長さ | 必要 |
| GET | `/strata/props` | 生成エンジンの設定(文脈長・サンプリング既定値など) | 必要 |
| GET | `/strata/slots` | 処理スロットの状態 | 必要 |
| GET | `/strata/metrics` | 監視値(JSON。Prometheus 形式も可) | 必要 |
| GET | `/strata/mcp-servers` | 生成エンジンに登録された MCP サーバとツール | 必要 |

## 4. モデル

| 論理名 | 種類 | 用途 | 主な仕様 |
|---|---|---|---|
| `large` | 生成(chat) | 要約・抽出・分類・コード・tool call・画像の読み取り・エージェント実行 | Qwen3.8-Flash-Next(125B MoE / アクティブ 6B)。文脈 131,072 トークン。画像入力可。**思考は既定で無効** |
| `embed` | 埋め込み | 類似検索・関連リンク | bge-m3。**1024 次元**。多言語(日本語良好) |

- 増減・差し替えがあり得るので、起動時に `/v1/catalog` で確認してください。**カタログに無い名前は呼ばないでください。**
- 旧名 `quality` / `quality-next` は 2026-11-13 に廃止しました。呼ぶと 400 になります。生成は `large` を使ってください。

## 5. モデルカタログ `GET /v1/catalog`

**載っているモデルは必ず呼べます**(実体が起動・取得済みであることを確認しています)。生成エンジンが再起動中のあいだは、`large` が一時的に一覧から消えます。

```bash
curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"
```

応答(抜粋):

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
      "purpose": "生成全般(要約・抽出・分類・コード・tool call・画像の読み取り・エージェント実行)。ハブの既定モデル",
      "context_length": 131072,
      "thinking": {
        "enabled_by_default": false,
        "note": "思考を有効にすると message.reasoning_content に入る。最終回答は message.content。",
        "enable": "リクエストボディに \"reasoning_effort\": \"low\" / \"medium\" / \"high\"",
        "disable": "既定で無効(ハブが \"reasoning_effort\": \"none\" を補う)",
        "budget": "\"reasoning_budget_tokens\": N で思考を N トークン前後で打ち切って回答に移らせる"
      },
      "options": { "reasoning_effort": "...", "reasoning_budget_tokens": "...", "sampling": "...", "images": "...", "tools": "..." },
      "native_api": { "base_path": "/strata", "auth": "...", "endpoints": { "messages": "POST /strata/v1/messages(...)" } },
      "notes": "生成 48〜79 tok/s、prefill 1200〜1700 tok/s(2026-11-13 実測)。...",
      "input_modalities": ["text", "image"],
      "capabilities": { "tools": true, "parallel_tool_calls": true, "system_role": true, "vision": true },
      "sampling_defaults": { "temperature": 1.0, "top_p": 0.95, "top_k": 20 },
      "parallel_requests": 1,
      "engine": "Strata 0.1.59"
    },
    {
      "id": "embed",
      "object": "model",
      "kind": "embedding",
      "endpoint": "/v1/embeddings",
      "installed": true,
      "backend": "bge-m3:latest",
      "purpose": "類似検索・関連リンク用の埋め込みベクトル",
      "dimensions": 1024,
      "notes": "多言語(日本語良好)。索引は次元 1024 で設計すること"
    }
  ]
}
```

| フィールド | 意味 |
|---|---|
| `id` | リクエストの `model` に渡す論理名。**クライアントが使うのはこれだけ** |
| `kind` | `chat` → `/v1/chat/completions`、`embedding` → `/v1/embeddings` |
| `endpoint` | そのモデルを呼ぶパス |
| `context_length` | 入力 + 思考 + 出力の合計上限(トークン)。生成エンジンの実際の設定値 |
| `thinking` | 思考の既定・有効化の方法・上限の付け方 |
| `options` | 使えるリクエストオプション(§6.3) |
| `native_api` | `/strata/*` の一覧(§8) |
| `input_modalities` | 受け付ける入力。`image` があれば画像を送れる |
| `capabilities` | tool・並列 tool call・system ロール・画像への対応 |
| `sampling_defaults` | サンプリングを省略したときに使われる値 |
| `parallel_requests` | 同時に処理できるリクエスト数。1 なら後続は順番待ち |
| `dimensions` | 埋め込みの次元数 |
| `backend` / `engine` | 参考情報。**依存しないこと**(差し替わります) |

`GET /v1/catalog?all=1` は、設定済みでも今は呼べないモデルを `installed: false` と `unavailable_reason` 付きで返します(診断用)。

## 6. 生成 `POST /v1/chat/completions`

OpenAI Chat Completions と同じ形です。

### 6.1 リクエスト

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `model` | string | ✅ | `large` |
| `messages` | array | ✅ | `system` / `user` / `assistant` / `tool`。会話履歴を毎回全部入れる |
| `max_tokens` | int | | 出力の上限。思考を有効にするときは大きめに(4096 以上推奨) |
| `stream` | bool | | `true` で SSE |
| `stream_options` | object | | `{"include_usage": true}` で最後に `usage` を流す |
| `tools` / `tool_choice` | | | function calling。並列 tool call 可 |
| `reasoning_effort` | string | | `none`(既定) / `low` / `medium` / `high` |
| `temperature` / `top_p` | number | | サンプリング |
| `stop` | string / array | | 停止文字列 |
| その他の Strata オプション | | | §6.3 |

### 6.2 応答

```json
{
  "id": "chatcmpl-f5c73b6bbc194daf87445a1e",
  "created": 1794581118,
  "model": "large",
  "object": "chat.completion",
  "choices": [
    {
      "finish_reason": "stop",
      "index": 0,
      "message": { "content": "東京", "role": "assistant" }
    }
  ],
  "usage": {
    "completion_tokens": 2, "prompt_tokens": 20, "total_tokens": 22,
    "prompt_tokens_details": { "cached_tokens": 13 }
  },
  "timings": {
    "cache_n": 13, "prompt_n": 7, "prompt_ms": 228.7, "prompt_per_second": 30.6,
    "predicted_n": 2, "predicted_ms": 96.4, "predicted_per_second": 20.7,
    "draft_n": 5, "draft_n_accepted": 1
  }
}
```

- **思考を有効にした場合**、思考過程は `message.reasoning_content`、最終回答は `message.content` に入ります。
  ```json
  "message": { "content": "391", "role": "assistant",
               "reasoning_content": "The user asks for 17 × 23 ... = 391\n" }
  ```
- `finish_reason`: `stop`(完了) / `length`(`max_tokens` に達した) / `tool_calls`(tool 呼び出し)。
- `timings` は OpenAI 標準外の追加フィールドです(読み込み・生成のトークン数、時間、速度)。無視してかまいません。
- `prompt_tokens_details.cached_tokens` は、前回と共通の先頭部分が再利用されたトークン数です。

### 6.3 Strata のオプション(`large`)

OpenAI 標準外のオプションも、ボディに書けば効きます(OpenAI SDK では `extra_body` で渡す)。

| オプション | 説明 |
|---|---|
| `reasoning_effort` | `"none"` / `"low"` / `"medium"` / `"high"`。**既定は `none`(思考なし)** |
| `reasoning` | `{"effort": "..."}`。Responses 形式の書き方で、意味は `reasoning_effort` と同じ |
| `chat_template_kwargs` | `{"enable_thinking": false}` で思考を止める |
| `reasoning_budget_tokens` | 思考の上限トークン数。超えると短い締めの一文で思考を終えて回答に移る(実測: 64 を指定すると思考の長さが約 1/3 になった) |
| `top_k` | 1〜64 |
| `min_p` / `seed` / `repetition_penalty` / `presence_penalty` / `frequency_penalty` | サンプリング。**`seed` や `temperature: 0` でも出力は完全には固定されません** |
| 画像 | `messages[].content` に `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` |

宣言していない tool を呼ぼうとした出力は tool call にならず、テキストのまま返ります。そのとき応答のトップレベルに `strata_rejected_tool_calls`(例: `[{"name": "bash", "reason": "undeclared"}]`)が付きます。

### 6.4 ストリーミング

`"stream": true` で OpenAI 互換の SSE が返ります。`choices[0].delta.content` を連結し、`data: [DONE]` で終わります。思考は `delta.reasoning_content` に流れます。最後のチャンクに `finish_reason` と `timings` が付きます。

```text
data: {"id":"chatcmpl-1abf...","object":"chat.completion.chunk","model":"large","choices":[{"index":0,"delta":{"content":"","role":"assistant"}}]}

data: {"id":"chatcmpl-1abf...","object":"chat.completion.chunk","model":"large","choices":[{"index":0,"delta":{"content":"こんにちは"}}]}

data: {"id":"chatcmpl-1abf...","object":"chat.completion.chunk","model":"large","choices":[{"finish_reason":"length","index":0,"delta":{}}],"timings":{...}}

data: [DONE]
```

ストリームを途中で切断すると、ハブは約1秒で生成を止めます。不要になった生成は接続を閉じて止めてください。

### 6.5 tool call

```json
"choices": [{
  "finish_reason": "tool_calls",
  "message": {
    "role": "assistant", "content": null,
    "tool_calls": [{
      "id": "call_e226ba902a9049e4a658881b", "type": "function",
      "function": { "name": "get_weather", "arguments": "{\"city\":\"\\u6771\\u4eac\"}" }
    }]
  }
}]
```

`arguments` は JSON 文字列です(非 ASCII は `\uXXXX` でエスケープされることがあります)。tool の結果は `{"role": "tool", "tool_call_id": "...", "content": "..."}` を履歴に足して送り返します。

### 6.6 例

Python(openai SDK):

```python
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ["AI_HUB_URL"] + "/v1", api_key=os.environ["AI_HUB_KEY"])

# 通常(思考なし・速い)
r = client.chat.completions.create(
    model="large", max_tokens=2048,
    messages=[{"role": "user", "content": "……を3行で要約して"}],
)
print(r.choices[0].message.content)

# 考えさせる + 思考に上限 + 画像
r = client.chat.completions.create(
    model="large", max_tokens=4096, reasoning_effort="medium",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "この画像の要点を3行で"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}},
    ]}],
    extra_body={"reasoning_budget_tokens": 512, "top_k": 20},
)
print(r.choices[0].message.content)
```

curl(Windows のシェルで日本語を送るときは、文字化けを避けるため標準入力で渡す):

```bash
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"model": "large", "max_tokens": 256,
 "messages": [{"role": "user", "content": "日本の首都は?"}]}
JSON
```

JavaScript(fetch):

```js
const r = await fetch(`${process.env.AI_HUB_URL}/v1/chat/completions`, {
  method: "POST",
  headers: { "Authorization": `Bearer ${process.env.AI_HUB_KEY}`, "Content-Type": "application/json" },
  body: JSON.stringify({ model: "large", max_tokens: 1024,
                         messages: [{ role: "user", content: "……を3行で要約して" }] }),
});
const j = await r.json();
console.log(j.choices[0].message.content);
```

## 7. 埋め込み `POST /v1/embeddings`

```python
r = client.embeddings.create(model="embed", input=["文1", "文2"])
vectors = [d.embedding for d in r.data]   # 各要素は長さ 1024 の float 配列
```

応答(抜粋):

```json
{
  "model": "embed",
  "object": "list",
  "data": [{ "object": "embedding", "index": 0, "embedding": [-0.015023228, 0.016655162, -0.02147924, "..."] }],
  "usage": { "prompt_tokens": 4, "total_tokens": 4 }
}
```

- 次元は **1024 固定**です。索引(pgvector / FAISS など)もこの次元で設計してください。類似度はコサイン類似度を推奨します。
- 埋め込みモデルを変えるときは事前に通知があります(再索引が必要になるため)。

## 8. Strata ネイティブ API `/strata/*`

生成エンジン(Strata)の API を、**同じ API キーで、変換せずに**使えます。

- 認証は `Authorization: Bearer <API キー>` か `x-api-key: <API キー>`。
- `model` には `large` を指定してください(他の名前でも同じモデルが答えます)。
- **どの形式でも、思考の強さを指定しなければ「思考なし」になります。**
- 一覧にないパスは 404 です(応答の `allowed` に使えるパスが並びます)。

### 8.1 Anthropic Messages `POST /strata/v1/messages`

Anthropic SDK の `base_url` を `$AI_HUB_URL/strata` にすれば、そのまま使えます。

```python
import anthropic
client = anthropic.Anthropic(base_url=os.environ["AI_HUB_URL"] + "/strata",
                             api_key=os.environ["AI_HUB_KEY"])
r = client.messages.create(
    model="large", max_tokens=4096,
    messages=[{"role": "user", "content": "……の原因を推理して"}],
    thinking={"type": "enabled", "budget_tokens": 1024},   # 省略すると思考なし
)
for block in r.content:
    print(block.type)   # thinking → text
```

| 思考の指定 | 意味 |
|---|---|
| (省略) | 思考なし |
| `"thinking": {"type": "disabled"}` | 思考なし |
| `"thinking": {"type": "enabled", "budget_tokens": N}` | 思考あり。N が 2K 未満なら low、8K 未満なら medium、それ以上なら high 相当。N は思考の上限にもなる |
| `"output_config": {"effort": "low" \| "medium" \| "high"}` | 思考の強さを直接指定 |

応答(思考なし):

```json
{
  "id": "msg_6570e4e8a53247e59023d519",
  "type": "message",
  "role": "assistant",
  "model": "qwen3.8-flash-next-iq3_s",
  "content": [{ "type": "text", "text": "東京" }],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": { "input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 6, "cache_read_input_tokens": 13 }
}
```

ストリーム(`"stream": true`)、tools、画像(`{"type": "image", "source": {"type": "base64", ...}}`)も使えます。`POST /strata/v1/messages/count_tokens` は `{"input_tokens": N}` を返します。

### 8.2 OpenAI Responses `POST /strata/v1/responses`

```python
from openai import OpenAI
client = OpenAI(base_url=os.environ["AI_HUB_URL"] + "/strata/v1", api_key=os.environ["AI_HUB_KEY"])
r = client.responses.create(model="large", input="日本の首都は？一語で。")
print(r.output_text)   # 東京
```

- **ステートレス**です。`previous_response_id` で前の応答から続けることはできません。履歴は `input` に全部入れてください。
- 思考は `"reasoning": {"effort": "low" | "medium" | "high"}`(既定は none)。
- ストリームでは `response.completed` イベントで終わります。

### 8.3 状態・設定の取得

| パス | 応答の例・内容 |
|---|---|
| `GET /strata/status` | `{"busy": false, "queued": 0, "phase": "answering", "prompt_tokens": 20, "generated": 1, "max_tokens": 131044, ...}`。重い処理を投げる前に混み具合を見るのに使う |
| `GET /strata/v1/models` | `{"data": [{"id": "qwen3.8-flash-next-iq3_s", "status": {"value": "loaded"}, "meta": {"n_ctx": 131072}, "architecture": {"input_modalities": ["text", "image"], ...}}]}` |
| `GET /strata/props` | 文脈長・サンプリング既定値・テンプレートの能力(tools 対応など)・同時処理数・エンジン版 |
| `GET /strata/slots` | `[{"id": 0, "n_ctx": 131072, "is_processing": false, "n_prompt_tokens": 20}]` |
| `GET /strata/metrics` | 監視値(JSON)。`Accept: text/plain` か `?format=prometheus` で Prometheus 形式(`vllm:num_requests_running`、`strata:live_tok_s` など vLLM 互換の名前) |
| `GET /strata/mcp-servers` | `{"servers": [], "tools": 0}`(生成エンジンに登録された MCP サーバ) |

## 9. エラー

| HTTP | 応答の例 | 原因 | 対処 |
|---|---|---|---|
| 401 | `Authentication Error, No api key passed in.` | キーを送っていない | `Authorization: Bearer <キー>` を付ける |
| 400 | `No connected db.` | **キーが間違っている**(誤ったキーではこのメッセージになる) | 正しいキーを使う |
| 400 | `Invalid model name passed in model=quality` | 存在しない論理名(タイプミス・廃止名) | `/v1/catalog` の `id` を使う |
| 400 | `model=None` など | `model` 未指定、または JSON が壊れている(文字化けを含む) | `model` を指定。文字コードを確認(§6.6 の curl) |
| 404 | `not relayed: GET /...`(`strata_relay_error`) | `/strata/*` の一覧にないパス | 応答の `allowed` を見る |
| 413 | `invalid or too large request body` | `/strata/*` への 64MB を超えるボディ | 画像を縮小する |
| 500 / 502 | | 生成エンジンの起動中・再起動中(約1分) | `/v1/catalog` に `large` が戻るのを待ってリトライ |
| タイムアウト | | 長文の読み込み・長い生成・他のリクエストの順番待ち | タイムアウトを延ばしてリトライ |

## 10. 制約と性能

- **同時に処理するのは1リクエストだけ**です。同時に送ると順番待ちになります。クライアント側で同時実行数を絞ってください。
- 速度の目安(2026-11-13 実測):
  - 生成: 48〜79 トークン/秒(日本語の文章は 50 前後、コードは 70 前後)
  - 入力の読み込み: 1,200〜1,700 トークン/秒(1.5 万トークンで約 9 秒。10 万トークンなら1分以上)
- **タイムアウトは 120 秒以上、長文の入力なら 600 秒**を推奨します(ハブ側の上限は 900 秒)。
- 文脈長は 131,072 トークン(入力 + 思考 + 出力の合計)。「履歴全体 + `max_tokens`」がこれに収まるように送ってください。
- 前回と先頭が共通する入力は再利用されて速くなります(`cached_tokens`)。system プロンプトや履歴の先頭は毎回同じにするのが有利です。
- OpenAI 固有で非対応のパラメータは無視されます(エラーにはなりません)。

## 11. 実装チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を環境変数で受け取る(キーをコードに書かない・`.env` は `.gitignore`)
- [ ] 起動時に `/v1/catalog` でモデルを確認し、カタログに無い `id` は呼ばない
- [ ] 会話履歴を自前で持ち、毎回 `messages`(Responses なら `input`)に全部入れる
- [ ] タイムアウトを 120 秒以上(長文なら 600 秒)にし、同時リクエスト数を絞る
- [ ] 思考は必要なときだけ有効にする(`reasoning_effort` / `thinking`)。そのときは `max_tokens` を大きめにし、`content` が空のケースを扱う
- [ ] 埋め込みは 1024 次元で索引を設計する
- [ ] 不要になったストリームは接続を閉じて止める
- [ ] 起動直後に次の3つが通ることを確かめる:

```bash
curl $AI_HUB_URL/health/liveliness                                  # → "I'm alive!"
curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"  # → large と embed
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  -d '{"model":"large","messages":[{"role":"user","content":"ping"}],"max_tokens":32}'
```
