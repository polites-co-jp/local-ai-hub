# ai-hub クライアント連携ガイド

LAN内のローカルLLM推論ハブ「ai-hub」に、外部アプリケーションから接続するための手順書。
**このドキュメントは、別アプリを実装するエージェント/開発者向けのインプット。** ハブ側(サーバ)の実装は完了済みで、クライアントは「OpenAI互換APIを叩くだけ」でよい。

---

## 1. これは何か

- ai-hub = **OpenAI互換APIのエンドポイント1つ**の裏に、ローカルLLM(生成)と埋め込みモデルを束ねた共有推論サービス。
- クライアントは **論理モデル名**(`quality` / `quality-next` / `embed`)だけを指定する。実体(`qwen3:14b` 等)はハブ側で隠蔽され、差し替えられてもクライアントは無変更でよい。使える論理名は `GET /v1/catalog` で実行時に取得できる。
- **ハブは状態を持たない(ステートレス)。** 会話履歴はクライアントが保持し、毎回まるごと送る。サーバ側にスレッド・記憶はない。
- 公開範囲は **プライベートLAN内のみ**。インターネット非公開。

---

## 2. 接続情報

| 項目 | 値 |
|---|---|
| ベースURL(LAN内の別マシンから) | `http://192.168.1.111:20800` |
| ベースURL(ハブと同じマシンから) | `http://localhost:20800` |
| OpenAI SDK 用 base_url | 上記に **`/v1`** を付ける(例 `http://192.168.1.111:20800/v1`) |
| 認証 | HTTPヘッダ `Authorization: Bearer <APIキー>` |
| プロトコル | HTTP(LAN内のみ・TLSなし) |

> `192.168.1.111` はハブ機の現在のLAN IP。IPが変わった場合は管理者(ハブ機)で確認して差し替える。

### APIキー(シークレット)

```
sk-aihub-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX   # ← 実値はリポジトリに載せない
```

> **実キーの入手:** ハブ機 `l-llm-containers/.env` の `LITELLM_MASTER_KEY`、または管理者から口外で受け取る。
> このドキュメントには実キーを書かない(GitHub 等に履歴として残るため)。

⚠️ **取り扱い厳守:**
- この鍵はLANアクセスの唯一の関門。**ソースにハードコードせず、環境変数 or `.env` で持つ。**
- クライアントの `.env` は **必ず `.gitignore` に入れ、リポジトリにコミットしない。**
- 鍵がローテーションされたら、新しい値に差し替える。

### 推奨する環境変数

```dotenv
AI_HUB_URL=http://192.168.1.111:20800
AI_HUB_KEY=sk-aihub-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

---

## 3. 利用できる論理モデル

| 論理名 | 実体 | 用途 | 備考 |
|---|---|---|---|
| `quality` | Qwen3 14B | 要約・分類・タグ付け・生成全般(高品質) | **推論(thinking)モデル**。§6 参照 |
| `quality-next` | Qwen3.5 9B | `quality` の後継候補。日本語の要約・テーマ抽出 | **評価中**。thinking モデルだが止め方が違う(§6-2) |
| `embed` | bge-m3 | 類似検索・関連リンク用の埋め込みベクトル | **次元数 = 1024**、多言語(日本語良好) |

選び方の目安:
- 日本語の要約・テーマ抽出 → `quality-next`(速い・VRAM に余裕)。思考は `"think": false` で止める。
- じっくり品質が要る生成・推論的タスク → `quality`。
- ベクトル化 → `embed`。

> **この表をハードコードしないこと。** モデルは増減する。実行時に **`GET /v1/catalog`** を叩けば、
> **その時点で実際に呼べるモデルだけ**が用途・思考の止め方・次元数付きで返る(§3.1)。
> `GET /v1/models` は設定に書かれた論理名を返すだけで、実体が未取得のモデルも含まれる(呼ぶと 500)。

### 3.1 カタログAPI — 使えるモデルを実行時に取得する

```bash
curl http://192.168.1.111:20800/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"
```

```json
{"object":"list","data":[
  {"id":"quality-next","kind":"chat","endpoint":"/v1/chat/completions","installed":true,
   "backend":"qwen3.5:9b","purpose":"日本語の要約・テーマ抽出","context_length":32768,
   "thinking":{"enabled_by_default":true,
               "disable":"リクエストボディに \"think\": false (または \"reasoning_effort\": \"none\")",
               "caution":"/no_think は効かない。抑制しないと簡単な質問でも 1 万トークン超を思考に費やす"}},
  {"id":"embed","kind":"embedding","endpoint":"/v1/embeddings","installed":true,"dimensions":1024}
]}
```

- `id` … リクエストの `model` に渡す値。**クライアントが使うのはこれだけ。**
- `kind` … `chat` / `embedding`。呼び分けに使う。
- `thinking` … **モデルごとに思考の止め方が違う**ので必ず読む。
- `backend` … 実体名。参考情報であり、依存しないこと(ハブ側で差し替える)。
- `?all=1` … 未取得のモデルも `installed:false` と理由付きで返す(障害切り分け用)。

---

## 4. エンドポイント(OpenAI互換)

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/v1/chat/completions` | チャット/生成(`stream` 対応) |
| POST | `/v1/embeddings` | 埋め込みベクトル |
| GET | `/v1/catalog` | **実際に呼べるモデルの一覧(推奨)**。用途・思考の止め方・次元数付き(§3.1) |
| GET | `/v1/models` | OpenAI標準のモデル一覧。設定に書かれた論理名を返す(未取得モデルも含みうる) |
| GET | `/health/liveliness` | ヘルスチェック(認証不要・200を返す) |

---

## 5. 呼び出し例

### 5-1. curl(生成)

```bash
curl -X POST http://192.168.1.111:20800/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "quality-next",
    "think": false,
    "messages": [
      {"role": "system", "content": "あなたは簡潔に答えるアシスタント。"},
      {"role": "user", "content": "日本の首都は?"}
    ],
    "max_tokens": 256
  }'
```

> ⚠️ **Windows(Git Bash / PowerShell)で curl を使うときの注意。**
> `-d '...'` のコマンドライン引数に**日本語を直接書くと文字化けして JSON が壊れ**、
> `400 Invalid model name passed in model=None` になる(ハブ側の問題ではなくシェルの文字コード)。
> 日本語を含むリクエストは標準入力から渡す:
> ```bash
> curl -X POST http://192.168.1.111:20800/v1/chat/completions \
>   -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
>   --data-binary @- <<'JSON'
> {"model":"quality-next","think":false,"messages":[{"role":"user","content":"日本の首都は?"}],"max_tokens":256}
> JSON
> ```
> SDK(Python / Node)経由なら発生しない。

レスポンス(抜粋):
```json
{
  "choices": [
    { "message": { "role": "assistant", "content": "東京です。" }, "finish_reason": "stop" }
  ],
  "usage": { "prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30 }
}
```

### 5-2. Python(openai SDK)

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["AI_HUB_URL"] + "/v1",
    api_key=os.environ["AI_HUB_KEY"],
)

# 会話履歴はクライアントが保持し、毎回まるごと送る
history = [
    {"role": "system", "content": "簡潔に答えて。"},
    {"role": "user", "content": "富士山の高さは?"},
]

resp = client.chat.completions.create(
    model="quality-next",
    messages=history,
    max_tokens=512,
    extra_body={"think": False},   # 思考を止める(Qwen3.5 は /no_think が効かない)
)
answer = resp.choices[0].message.content
print(answer)

# 次のターンも全文を送る
history.append({"role": "assistant", "content": answer})
history.append({"role": "user", "content": "それは世界何位?"})
resp2 = client.chat.completions.create(
    model="quality-next", messages=history, max_tokens=512, extra_body={"think": False})
print(resp2.choices[0].message.content)
```

### 5-3. Node.js / TypeScript(openai SDK)

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.AI_HUB_URL + "/v1",
  apiKey: process.env.AI_HUB_KEY,
});

const history = [
  { role: "system", content: "簡潔に答えて。" },
  { role: "user", content: "TypeScript とは?" },
];

const resp = await client.chat.completions.create({
  model: "quality-next",
  messages: history,
  max_tokens: 512,
  // @ts-expect-error ハブ独自: Qwen3.5 の思考抑制
  think: false,
});
console.log(resp.choices[0].message.content);
```

### 5-4. ストリーミング(SSE)

CPU/GPU負荷で応答に時間がかかる場合に体感が良い。`stream: true` を付けると OpenAI 互換の SSE が返る。

Python:
```python
stream = client.chat.completions.create(
    model="quality",
    messages=history,
    max_tokens=1024,
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="", flush=True)
```

素のfetch(ブラウザ/Node)で叩く場合は、`data: {...}` 行を逐次パースし、`choices[0].delta.content` を連結する。終端は `data: [DONE]`。

### 5-5. 埋め込み

```python
resp = client.embeddings.create(model="embed", input="埋め込みにしたい文章")
vec = resp.data[0].embedding   # 長さ 1024 の float 配列
```

複数まとめて:
```python
resp = client.embeddings.create(model="embed", input=["文1", "文2", "文3"])
vectors = [d.embedding for d in resp.data]
```

> 類似度はコサイン類似度を推奨。次元は **1024 固定**。索引(pgvector / FAISS 等)の次元設定もこれに合わせる。

---

## 6. 重要な挙動・制約(必ず読む)

1. **`quality`(Qwen3 14B)は推論モデル。**
   - 応答に「思考過程」が含まれ、OpenAI互換レスポンスでは `message.reasoning_content`(ストリーミングでは `delta.reasoning_content`)に入る。最終回答は `message.content`。
   - **`max_tokens` が小さいと思考の途中で打ち切られ `content` が空になる**ことがある。`quality` を使うときは `max_tokens` を十分に(目安 1024 以上)。
   - 思考を省きたい単純タスクは、ユーザメッセージ末尾に **`/no_think`** を付けると Qwen3 の思考を抑制できる。
   - **コンテキスト窓 = 32,768 トークン**(入力 messages + 思考 + 出力 の合計)。ハブ側で `OLLAMA_CONTEXT_LENGTH=32768` を設定済み。これを超える入力は古い側から切り捨てられ、出力も途中で止まる。長文を投げるときは「履歴全文 + max_tokens」が 32k 以内に収まるよう調整する。`max_tokens` 未指定なら窓いっぱいまで生成する。
2. **`quality-next`(Qwen3.5 9B)も推論モデルだが、思考の止め方が違う。**
   - **`/no_think` は効かない。** Qwen3 の書式を付けても思考は走り、簡単な質問でも 1万トークン以上を思考に消費して `max_tokens` 内に `content` が入らないことがある。
   - 思考を止めるには **リクエストボディに `"think": false`** (または `"reasoning_effort": "none"`)を指定する。要約・テーマ抽出のような定型タスクはこれで十分な品質が出て、応答も数秒に収まる。
   - `"chat_template_kwargs": {"enable_thinking": false}` は**効かない**(ハブ側で無視される)。
   - 思考を使う場合(難しい推論タスク)は `max_tokens` を大きく取るか未指定にする。`quality` と同じ注意が当てはまる。
3. **GPUは1枚=実質直列。** 同時に複数リクエストを投げると順番待ちになる。クライアント側で同時実行数を絞る/リトライ&タイムアウトを設ける。
4. **タイムアウトは長めに。** 初回モデルロードや長文生成で時間がかかる。HTTPクライアントのタイムアウトは **60〜120秒以上**を推奨(ストリーミングなら緩和される)。
5. **ステートレス。** サーバは会話を覚えない。コンテキストは毎回 `messages` に全部入れて送る(このハブの設計方針)。
6. **OpenAI互換だが実体はローカルモデル。** OpenAI固有の一部パラメータは無視される(ハブ側で `drop_params` 有効)。`temperature` / `top_p` / `max_tokens` / `stream` 等の基本パラメータは有効。
7. **LAN内のみ。** インターネット経由では到達しない。クライアントはハブと同一LANに居ること。

---

## 7. エラー早見表

| HTTP | 原因 | 対処 |
|---|---|---|
| 401 | `Authorization` ヘッダ無し/キー誤り | `Bearer <正しいキー>` を付ける |
| 400 `model=None ...` | `model` 未指定/JSONボディ不正 | `model` に `quality`/`quality-next`/`embed` を指定、Content-Type を `application/json` に |
| 400 `Invalid model name` | 論理名のタイプミス | `/v1/catalog` で正しい名前を確認 |
| 500 `model 'xxx' not found` | 論理名は設定にあるが実体が未取得 | ハブ管理者に連絡。`/v1/catalog` に載っているモデルを使う |
| 500 `cudaMalloc failed: out of memory` | VRAM 不足(特に `quality`)。他モデルが常駐中/GPUを他アプリが使用中 | リトライ、または `quality-next`(VRAM に余裕あり)に切替 |
| 502 / タイムアウト | モデルロード中・長文生成・GPU混雑 | タイムアウト延長・リトライ |

---

## 8. 接続確認(最初にこれだけ実行)

```bash
# 1) ヘルス(キー不要)
curl http://192.168.1.111:20800/health/liveliness          # -> "I'm alive!"

# 2) 使えるモデル一覧(キー必要)
curl http://192.168.1.111:20800/v1/catalog \
  -H "Authorization: Bearer $AI_HUB_KEY"     # -> quality / quality-next / embed

# 3) 生成のスモークテスト
curl -X POST http://192.168.1.111:20800/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  -d '{"model":"quality-next","think":false,"messages":[{"role":"user","content":"ping"}],"max_tokens":64}'
```

3つとも通れば、クライアント実装に進んでよい。

---

## 9. クライアント実装チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を環境変数で受け取る(ハードコード禁止)
- [ ] `.env` を `.gitignore` に追加(キーをコミットしない)
- [ ] OpenAI互換SDK or HTTP で `base_url = AI_HUB_URL + "/v1"` を設定
- [ ] 会話は履歴を保持し毎回 `messages` に全文を入れて送る
- [ ] HTTPタイムアウトを 60〜120秒以上に
- [ ] 同時リクエスト数を絞る(GPU1枚=直列)
- [ ] 起動時に `/v1/catalog` を叩き、使えるモデルを確認(表をハードコードしない)
- [ ] 用途でモデルを使い分け(`quality-next` / `quality` / `embed`)
- [ ] thinking モデルは `max_tokens` を十分に取り、`reasoning_content` の存在と `content` が空になるケースを考慮
- [ ] 思考の止め方はモデルごとに違う(`quality`=`/no_think` / `quality-next`=`"think": false`)
- [ ] 埋め込みは次元 **1024** で索引を設計
