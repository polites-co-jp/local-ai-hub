# ai-hub API アクセス手順書(クライアント向け)

LAN内のローカルLLM推論ハブ **ai-hub** に外部アプリケーションから接続するための手順書。
**この文書は、クライアントを実装するAIエージェント/開発者へのインプットである。** サーバ側は実装済みであり、クライアントは OpenAI 互換 API を叩くだけでよい。この文書の情報だけで接続できる。

---

## 1. 前提(3行で)

- ai-hub は **OpenAI 互換 API のエンドポイント1つ**の裏に、生成モデルと埋め込みモデルを束ねた共有推論サービス。
- クライアントは**論理モデル名**(`quality` / `quality-next` / `embed`)だけを指定する。実体モデルはハブ側で隠蔽され、差し替えられてもクライアントは無変更でよい。
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
| `backend` | 実体名。参考情報。**依存しないこと**(ハブ側で差し替わる) |

現在の論理モデル(増減するので、起動時にカタログで確認しハードコードしない):

| 論理名 | 用途 | 思考(thinking)の扱い |
|---|---|---|
| `quality` | 要約・分類・タグ付け・生成全般(高品質) | 既定で思考する。止めるにはユーザメッセージ末尾に `/no_think` |
| `quality-next` | 日本語の要約・テーマ抽出(qualityの後継候補) | 既定で思考する。**`/no_think` は効かない。** ボディに `"think": false` を入れる |
| `embed` | 類似検索・関連リンク用の埋め込み | ―(次元 **1024** 固定) |

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
    model="quality-next",
    messages=history,
    max_tokens=2048,
    extra_body={"think": False},   # Qwen3.5 系の思考抑制(/no_think は効かない)
)
print(resp.choices[0].message.content)
# 次のターンは history に assistant 応答と次の user 発話を append して全文を再送する
```

### 5-2. 生成(curl)

```bash
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"model":"quality-next","think":false,
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

## 6. 必ず守る挙動・制約

1. **thinking モデルの応答構造:** 思考過程は `message.reasoning_content`、最終回答は `message.content` に入る。**`max_tokens` が小さいと思考の途中で打ち切られ `content` が空になる**。生成では `max_tokens` を 1024 以上(推奨 2048)取り、`content` が空のケースを扱うこと。
2. **思考の止め方はモデルごとに違う**(§4 の表)。カタログの `thinking.disable` に従う。`quality-next` は抑制しないと簡単な質問でも1万トークン超を思考に費やす。
3. **コンテキスト窓 = 32,768 トークン**(入力 + 思考 + 出力の合計)。「履歴全文 + max_tokens」がこれに収まるように送る。
4. **GPU 1枚 = 実質直列。** 同時リクエストは順番待ちになる。クライアント側で同時実行数を絞る。
5. **タイムアウトは 60〜120 秒以上。** モデルのコールドスタート(数十秒)や長文生成があるため。別の生成モデルに切り替えた直後は特に遅い(モデル積み替えが起きる)。頻繁なモデル往復は避ける。
6. **OpenAI 互換だが実体はローカルモデル。** OpenAI 固有の未対応パラメータはサーバ側で無視される。`temperature` / `top_p` / `max_tokens` / `stream` は有効。

## 7. エラー早見表

| HTTP | 原因 | 対処 |
|---|---|---|
| 401 | キー無し/誤り | `Authorization: Bearer <正しいキー>` を付ける |
| 400 `model=None ...` | `model` 未指定 or JSON ボディ不正(文字化け含む) | `model` を指定。Content-Type と文字コードを確認(§5-2) |
| 400 `Invalid model name` | 論理名のタイプミス | `/v1/catalog` で正しい `id` を確認 |
| 500 `model 'xxx' not found` | 論理名はあるが実体が未取得 | `/v1/catalog` に載っているモデルだけを使う。載っていなければハブ管理者に連絡 |
| 500 `cudaMalloc failed: out of memory` | VRAM 不足(特に `quality`) | リトライ、または `quality-next` に切替 |
| 502 / タイムアウト | モデルロード中・長文生成・GPU 混雑 | タイムアウト延長・リトライ |

## 8. 接続確認(最初にこの3つを実行)

```bash
curl $AI_HUB_URL/health/liveliness                                    # 1) キー不要 → "I'm alive!"
curl $AI_HUB_URL/v1/catalog -H "Authorization: Bearer $AI_HUB_KEY"    # 2) モデル一覧が返る
curl -X POST $AI_HUB_URL/v1/chat/completions \
  -H "Authorization: Bearer $AI_HUB_KEY" -H "Content-Type: application/json" \
  -d '{"model":"quality-next","think":false,"messages":[{"role":"user","content":"ping"}],"max_tokens":64}'
```

3つとも通れば実装に進んでよい。

## 9. 実装チェックリスト

- [ ] `AI_HUB_URL` / `AI_HUB_KEY` を環境変数で受け取る(ハードコード禁止・`.env` は `.gitignore`)
- [ ] 起動時に `/v1/catalog` でモデルを確認(カタログに無い `id` は呼ばない)
- [ ] 会話履歴を自前で保持し、毎回 `messages` に全文を入れて送る
- [ ] HTTP タイムアウト 60〜120 秒以上、同時リクエスト数を絞る
- [ ] thinking モデル: `max_tokens` を十分に取り、`reasoning_content` の存在と `content` が空のケースを扱う
- [ ] 思考の止め方をモデルごとに使い分ける(`quality`=`/no_think`、`quality-next`=`"think": false`)
- [ ] 埋め込みは次元 **1024** で索引を設計
