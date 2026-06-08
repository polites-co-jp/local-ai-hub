# ai-hub クライアント連携ガイド

LAN内のローカルLLM推論ハブ「ai-hub」に、外部アプリケーションから接続するための手順書。
**このドキュメントは、別アプリを実装するエージェント/開発者向けのインプット。** ハブ側(サーバ)の実装は完了済みで、クライアントは「OpenAI互換APIを叩くだけ」でよい。

---

## 1. これは何か

- ai-hub = **OpenAI互換APIのエンドポイント1つ**の裏に、ローカルLLM(生成)と埋め込みモデルを束ねた共有推論サービス。
- クライアントは **論理モデル名**(`quality` / `embed` / `fast`)だけを指定する。実体(`qwen3:14b` 等)はハブ側で隠蔽され、差し替えられてもクライアントは無変更でよい。
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
| `fast` | Llama 3.1 8B | 速度優先の軽量タスク・単純な応答 | 推論を挟まず素直に返答 |
| `gemma` | Gemma4 12B | 比較・動作確認用の汎用生成 | オンデマンド(初回コールドスタートあり) |
| `embed` | bge-m3 | 類似検索・関連リンク用の埋め込みベクトル | **次元数 = 1024**、多言語(日本語良好) |

選び方の目安:
- 単純なチャット/分類/抽出 → まず `fast`(速い)。
- 品質が要る要約・推論的タスク → `quality`。
- ベクトル化 → `embed`。

---

## 4. エンドポイント(OpenAI互換)

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/v1/chat/completions` | チャット/生成(`stream` 対応) |
| POST | `/v1/embeddings` | 埋め込みベクトル |
| GET | `/v1/models` | 利用可能な論理モデル一覧 |
| GET | `/health/liveliness` | ヘルスチェック(認証不要・200を返す) |

---

## 5. 呼び出し例

### 5-1. curl(生成)

```bash
curl -X POST http://192.168.1.111:20800/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "fast",
    "messages": [
      {"role": "system", "content": "あなたは簡潔に答えるアシスタント。"},
      {"role": "user", "content": "日本の首都は?"}
    ],
    "max_tokens": 256
  }'
```

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
    model="fast",
    messages=history,
    max_tokens=512,
)
answer = resp.choices[0].message.content
print(answer)

# 次のターンも全文を送る
history.append({"role": "assistant", "content": answer})
history.append({"role": "user", "content": "それは世界何位?"})
resp2 = client.chat.completions.create(model="fast", messages=history, max_tokens=512)
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
  model: "fast",
  messages: history,
  max_tokens: 512,
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
   - 思考を省きたい単純タスクは **`fast` を使う**か、ユーザメッセージ末尾に `/no_think` を付けると Qwen3 の思考を抑制できる。
2. **GPUは1枚=実質直列。** 同時に複数リクエストを投げると順番待ちになる。クライアント側で同時実行数を絞る/リトライ&タイムアウトを設ける。
3. **タイムアウトは長めに。** 初回モデルロードや長文生成で時間がかかる。HTTPクライアントのタイムアウトは **60〜120秒以上**を推奨(ストリーミングなら緩和される)。
4. **ステートレス。** サーバは会話を覚えない。コンテキストは毎回 `messages` に全部入れて送る(このハブの設計方針)。
5. **OpenAI互換だが実体はローカルモデル。** OpenAI固有の一部パラメータは無視される(ハブ側で `drop_params` 有効)。`temperature` / `top_p` / `max_tokens` / `stream` 等の基本パラメータは有効。
6. **LAN内のみ。** インターネット経由では到達しない。クライアントはハブと同一LANに居ること。

---

## 7. エラー早見表

| HTTP | 原因 | 対処 |
|---|---|---|
| 401 | `Authorization` ヘッダ無し/キー誤り | `Bearer <正しいキー>` を付ける |
| 400 `model=None ...` | `model` 未指定/JSONボディ不正 | `model` に `quality`/`fast`/`embed` を指定、Content-Type を `application/json` に |
| 400 `Invalid model name` | 論理名のタイプミス | `/v1/models` で正しい名前を確認 |
| 502 / タイムアウト | モデルロード中・長文生成・GPU混雑 | タイムアウト延長・リトライ・`fast` に切替 |

---

## 8. 接続確認(最初にこれだけ実行)

```bash
# 1) ヘルス(キー不要)
curl http://192.168.1.111:20800/health/liveliness          # -> "I'm alive!"

# 2) モデル一覧(キー必要)
curl http://192.168.1.111:20800/v1/models \
  -H "Authorization: Bearer $AI_HUB_KEY"                    # -> quality / embed / fast

# 3) 生成のスモークテスト
curl -X POST http://192.168.1.111:20800/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  -d '{"model":"fast","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
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
- [ ] 用途でモデルを使い分け(`fast` / `quality` / `embed`)
- [ ] `quality` 使用時は `max_tokens` を十分に取り、`reasoning_content` の存在を考慮
- [ ] 埋め込みは次元 **1024** で索引を設計
