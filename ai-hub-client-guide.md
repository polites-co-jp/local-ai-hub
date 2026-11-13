# ai-hub API アクセス手順書(クライアント向け)

LAN内のローカルLLM推論ハブ **ai-hub** に外部アプリケーションから接続するための手順書。
**この文書は、クライアントを実装するAIエージェント/開発者へのインプットである。** サーバ側は実装済みであり、クライアントは OpenAI 互換 API を叩くだけでよい。この文書の情報だけで接続できる。

---

## 1. 前提(3行で)

- ai-hub は **OpenAI 互換 API のエンドポイント1つ**の裏に、生成モデルと埋め込みモデルを束ねた共有推論サービス。
- クライアントは**論理モデル名**(`large` / `embed`)だけを指定する。実体モデルはハブ側で隠蔽され、差し替えられてもクライアントは無変更でよい。
- **ステートレス。** サーバは会話を覚えない。履歴はクライアントが保持し、毎回 `messages` に全文を入れて送る。

## 2. 接続情報

| 項目 | 値 |
|---|---|
| ベースURL(LAN内の別マシンから) | `http://192.168.1.111:20800` |
| ベースURL(ハブと同じマシンから) | `http://localhost:20800` |
| OpenAI SDK の `base_url` | 上記 + `/v1`(例 `http://192.168.1.111:20800/v1`) |
| 認証 | HTTP ヘッダ `Authorization: Bearer <APIキー>` |
| プロトコル | HTTP(LAN内のみ・TLSなし)。インターネット経由では到達しない |

APIキーの実値はこの文書に書かない。**ハブ管理者から受け取り**、環境変数で持つ:

```dotenv
AI_HUB_URL=http://192.168.1.111:20800
AI_HUB_KEY=sk-xxxx        # ハードコード禁止。.env は .gitignore に入れる
```

## 3. エンドポイント

| メソッド | パス | 用途 | 認証 |
|---|---|---|---|
| GET | `/health/liveliness` | 疎通確認 | 不要 |
| GET | `/v1/catalog` | **今すぐ呼べるモデルの一覧(推奨)** | 必要 |
| GET | `/v1/models` | OpenAI 標準の一覧。**未取得モデルも含まれ、呼ぶと 500 になりうる。使わない** | 必要 |
| POST | `/v1/chat/completions` | 生成(`stream: true` で SSE 対応) | 必要 |
| POST | `/v1/embeddings` | 埋め込みベクトル | 必要 |
| POST | `/strata/v1/messages` | **Anthropic Messages 形式**の生成(Strata ネイティブ。`x-api-key` でも可) | 必要 |
| POST | `/strata/v1/responses` | **OpenAI Responses 形式**の生成(Strata ネイティブ・ステートレス) | 必要 |
| POST | `/strata/v1/chat/completions` | Chat Completions を Strata に直接(LiteLLM の変換を通さない) | 必要 |
| GET | `/strata/status` / `/strata/props` / `/strata/metrics` | 生成モデルの混み具合・設定・監視値 | 必要 |

## 4. モデルの選び方 — まず `/v1/catalog` を叩く

```bash
curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"
```

**カタログに載っているモデルは必ず呼べる**(`/v1/models` と違い、実体の取得状況を確認済み)。返る各エントリの読み方:

| フィールド | 意味 |
|---|---|
| `id` | リクエストの `model` に渡す論理名。**クライアントが使うのはこれだけ** |
| `kind` | `chat` → `/v1/chat/completions`、`embedding` → `/v1/embeddings` |
| `context_length` | 入力 + 思考 + 出力の合計上限(トークン) |
| `thinking` | 思考モデルか・**思考の止め方**・注意点。**モデルごとに止め方が違うので必ず読む** |
| `dimensions` | 埋め込みの次元数(索引設計に使う) |
| `options` | そのモデルで使えるリクエストオプション(思考の上限・サンプリング・画像など)。§5-5 |
| `input_modalities` / `capabilities` | 受け付ける入力(`image` があれば画像可)と tool 対応。生成エンジンの実際の設定から自動で載る |
| `sampling_defaults` / `parallel_requests` | サンプリング省略時の値 / 同時に処理できるリクエスト数 |
| `native_api` | Strata ネイティブ API(`/strata/*`)の一覧。§5-6 |
| `backend` | 実体名。参考情報。**依存しないこと**(ハブ側で差し替わる) |

現在の論理モデル(増減するので、起動時にカタログで確認しハードコードしない):

| 論理名 | 用途 | 思考(thinking)の扱い |
|---|---|---|
| `large` | 生成全般(要約・抽出・分類・コード・tool call・エージェント実行)。125B MoE、文脈 128k | **既定で思考しない。** 考えさせたいときだけボディに `"reasoning_effort": "low"` / `"medium"` / `"high"` |
| `embed` | 類似検索・関連リンク用の埋め込み | ―(次元 **1024** 固定) |

> 2026-11-13 に `quality` / `quality-next` は廃止し、`large` に統合した。旧名を呼ぶと 400 になる。

## 5. 呼び出し方

### 5-1. 生成(Python / openai SDK)

```python
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ["AI_HUB_URL"] + "/v1",
                api_key=os.environ["AI_HUB_KEY"])

history = [
    {"role": "system", "content": "簡潔に答えて。"},
    {"role": "user", "content": "……を3行で要約して"},
]
resp = client.chat.completions.create(
    model="large",
    messages=history,
    max_tokens=2048,               # 既定で思考しないので、そのまま回答に使われる
)
print(resp.choices[0].message.content)
# 次のターンは history に assistant 応答と次の user 発話を append して全文を再送する

# 難しい問題だけ考えさせる(思考は message.reasoning_content に入る)
resp = client.chat.completions.create(
    model="large", messages=history, max_tokens=8192, reasoning_effort="medium",
)
```

### 5-2. 生成(curl)

```bash
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"model":"large",
 "messages":[{"role":"user","content":"日本の首都は?"}],"max_tokens":256}
JSON
```

> **Windows のシェルで日本語を `-d '...'` に直接書かない。** 文字化けで JSON が壊れ
> `400 Invalid model name passed in model=None` になる。上記のように標準入力
> (`--data-binary @-`)で渡す。SDK 経由なら発生しない。

### 5-3. ストリーミング

`stream: true` を付けると OpenAI 互換の SSE が返る。`data: {...}` 行の `choices[0].delta.content` を連結し、終端は `data: [DONE]`。思考トークンは `delta.reasoning_content` に流れてくる。

### 5-4. 埋め込み

```python
resp = client.embeddings.create(model="embed", input=["文1", "文2"])
vectors = [d.embedding for d in resp.data]   # 各要素は長さ 1024 の float 配列
```

次元は **1024 固定**。索引(pgvector / FAISS 等)もこの次元で設計する。類似度はコサイン類似度を推奨。

### 5-5. `large` のオプション(Strata)

`/v1/chat/completions` のボディにそのまま書けば効く。カタログの `options` にも同じ一覧がある。

```python
resp = client.chat.completions.create(
    model="large",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "この画像の要点を3行で"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}},   # 画像入力
    ]}],
    reasoning_effort="medium",                    # none / low / medium / high(既定 none)
    max_tokens=4096,
    extra_body={
        "reasoning_budget_tokens": 512,           # 思考を 512 トークン前後で打ち切って回答へ
        "top_k": 20, "min_p": 0.05,               # OpenAI 標準外のサンプリング
    },
)
print(resp.choices[0].message.content)
```

| オプション | 内容 |
|---|---|
| `reasoning_effort` / `reasoning: {"effort"}` / `chat_template_kwargs: {"enable_thinking": false}` | 思考の強さ・停止 |
| `reasoning_budget_tokens` | 思考の上限トークン数 |
| `temperature` / `top_p` / `top_k`(1〜64) / `min_p` / `seed` / `repetition_penalty` / `presence_penalty` / `frequency_penalty` | サンプリング。`seed` でも出力は完全には固定されない |
| `image_url`(data URI) | 画像入力 |
| `tools` / `tool_choice` | tool call(並列可)。宣言していない tool の呼び出しはテキストのまま返り、`strata_rejected_tool_calls` に名前が載る |

応答には OpenAI 標準外の `timings`(読み込み・生成の時間と速度)が付く。

### 5-6. Anthropic / Responses 形式で使う(`/strata/*`)

生成モデルのネイティブ API を、同じ API キーで変換なしに使える。思考の強さを指定しなければ「思考なし」になる。

```python
import anthropic
client = anthropic.Anthropic(base_url=os.environ["AI_HUB_URL"] + "/strata",
                             api_key=os.environ["AI_HUB_KEY"])
r = client.messages.create(
    model="large", max_tokens=4096,
    thinking={"type": "enabled", "budget_tokens": 1024},   # 省略すると思考なし
    messages=[{"role": "user", "content": "……の原因を推理して"}],
)
print([b.type for b in r.content])   # ['thinking', 'text']
```

- OpenAI Responses: `POST $AI_HUB_URL/strata/v1/responses`(`{"model": "large", "input": "..."}`)。ステートレスなので `previous_response_id` での継続はできない。
- 混み具合: `GET $AI_HUB_URL/strata/status` → `{"busy": ..., "queued": ...}`。重い処理を投げる前の確認に使える。
- 使えるパスの一覧はカタログの `native_api.endpoints`。それ以外のパスは 404。

## 6. 必ず守る挙動・制約

1. **思考の応答構造:** `reasoning_effort` で思考を有効にすると、思考過程は `message.reasoning_content`、最終回答は `message.content` に入る。**`max_tokens` が小さいと思考の途中で打ち切られ `content` が空になる**。思考を有効にするときは `max_tokens` を 4096 以上取り、`content` が空のケースを扱うこと。
2. **思考の扱いはカタログの `thinking` に従う**(§4 の表)。`large` は既定で思考しない(ハブが `"reasoning_effort": "none"` を補う)。
3. **コンテキスト窓 = 131,072 トークン**(入力 + 思考 + 出力の合計)。「履歴全文 + max_tokens」がこれに収まるように送る。
4. **`large` は1リクエストずつ処理する。** 同時リクエストは順番待ちになる。クライアント側で同時実行数を絞る。
5. **長文入力は読み込みに時間がかかる。** 読み込み速度は約 1200〜1700 トークン/秒(1.5 万トークンで約 10 秒、10 万トークンなら1分以上)。生成は 50〜78 トークン/秒。**タイムアウトは 120 秒以上、長文なら 600 秒**にする。
6. **OpenAI 互換だが実体はローカルモデル。** OpenAI 固有の未対応パラメータはサーバ側で無視される。`temperature` / `top_p` / `max_tokens` / `stream` は有効。

## 7. エラー早見表

| HTTP | 原因 | 対処 |
|---|---|---|
| 401 | キー無し/誤り | `Authorization: Bearer <正しいキー>` を付ける |
| 400 `model=None ...` | `model` 未指定 or JSON ボディ不正(文字化け含む) | `model` を指定。Content-Type と文字コードを確認(§5-2) |
| 400 `Invalid model name` | 論理名のタイプミス、または廃止した `quality` / `quality-next` を指定 | `/v1/catalog` で正しい `id` を確認(生成は `large`) |
| 500 `model 'xxx' not found` | 論理名はあるが実体が未取得 | `/v1/catalog` に載っているモデルだけを使う。載っていなければハブ管理者に連絡 |
| 500 / 502(`large`) | Strata の起動中・再起動中(起動に約1分) | `/v1/catalog` に `large` が戻るのを待ってリトライ |
| タイムアウト | 長文入力の読み込み・長文生成・他のリクエストの順番待ち | タイムアウト延長・リトライ |

## 8. 接続確認(最初にこの3つを実行)

```bash
curl $AI_HUB_URL/health/liveliness                                    # 1) キー不要 → "I'm alive!"
curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"    # 2) モデル一覧が返る
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  -d '{"model":"large","messages":[{"role":"user","content":"ping"}],"max_tokens":64}'
```

3つとも通れば実装に進んでよい。

## 9. 実装チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を環境変数で受け取る(ハードコード禁止・`.env` は `.gitignore`)
- [ ] 起動時に `/v1/catalog` でモデルを確認(カタログに無い `id` は呼ばない)
- [ ] 会話履歴を自前で保持し、毎回 `messages` に全文を入れて送る
- [ ] HTTP タイムアウト 120 秒以上(長文入力なら 600 秒)、同時リクエスト数を絞る(`large` は1件ずつ処理)
- [ ] 思考を有効にするとき: `max_tokens` を十分に取り、`reasoning_content` の存在と `content` が空のケースを扱う
- [ ] 思考は必要なときだけ `reasoning_effort` で有効にする(既定は思考なし)
- [ ] 埋め込みは次元 **1024** で索引を設計
