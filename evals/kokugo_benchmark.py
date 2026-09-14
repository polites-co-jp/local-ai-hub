#!/usr/bin/env python3
"""ai-hub の生成モデルに国語(現代文)の読解問題を解かせて正答率を比較する。

quality (Qwen3 14B) と quality-next (Qwen3.5 9B) を同じ設問セットに対して実行し、
正答率・所要時間・トークン数を比較する。設問は JSON ファイルで与える(形式は
evals/data/kokugo_sample.json を参照。実際のセンター試験・共通テストの本文は
著作権保護されているため、同梱しているのは動作確認用のオリジナル模擬設問のみ。
実データで比較したい場合は同じ形式で自分の手元の問題を用意して差し替えること)。

GPU 1枚 = 実質直列で、生成モデルを切り替えると退避+再ロード(コールドスタート
数十秒)が起きるため、設問ごとにモデルを往復せず、**モデルごとに全設問をまとめて
実行**してからまとめて比較する。

使い方:
  set AI_HUB_URL=http://192.168.1.111:20800
  set AI_HUB_KEY=sk-xxxx
  python evals/kokugo_benchmark.py evals/data/kokugo_sample.json
  python evals/kokugo_benchmark.py evals/data/kokugo_sample.json --no-think --models quality-next

標準ライブラリのみ。追加 pip 依存なし。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 300
ANSWER_RE = re.compile(r"最終回答[:：]\s*([1-9１-９])")
ZEN2HAN = str.maketrans("１２３４５６７８９", "123456789")


def resolve_key():
    key = os.environ.get("AI_HUB_KEY", "").strip()
    if key:
        return key
    # ローカル実行の便宜: ハブと同じマシンで動かす場合は .env から拾う
    env_path = os.path.join(os.path.dirname(__file__), "..", "l-llm-containers", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"LITELLM_MASTER_KEY=(.+)", line.strip())
                if m:
                    return m.group(1).strip()
    return ""


def build_prompt(q):
    lines = []
    if q.get("passage"):
        lines.append("【本文】\n" + q["passage"])
    lines.append("【問題】\n" + q["question"])
    lines.append("【選択肢】")
    for key in sorted(q["choices"], key=int):
        lines.append(f"{key}. {q['choices'][key]}")
    lines.append(
        "\n選択肢の中から最も適切なものを1つ選び、根拠を簡潔に述べたうえで、"
        "最後に必ず「最終回答: <番号>」という形式で1行にまとめて答えてください。"
    )
    return "\n".join(lines)


def extract_answer(text):
    m = ANSWER_RE.search(text or "")
    if not m:
        return None
    return m.group(1).translate(ZEN2HAN)


class Client:
    def __init__(self, hub_url, key, max_tokens, think):
        self.hub_url = hub_url.rstrip("/")
        self.key = key
        self.max_tokens = max_tokens
        self.think = think  # None=モデル既定のまま / False=思考を止める

    def ask(self, model, question):
        content = build_prompt(question)
        body = {"model": model, "messages": [{"role": "user", "content": content}],
                "max_tokens": self.max_tokens}
        if self.think is False:
            if model == "quality":
                # Qwen3 系は /no_think をユーザメッセージ末尾に付けると思考を抑制できる
                body["messages"][0]["content"] += "\n/no_think"
            else:
                # Qwen3.5 系は /no_think が効かないため think:false を明示する
                body["think"] = False

        req = urllib.request.Request(
            self.hub_url + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - t0
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        return text, elapsed, usage


def run(client, models, questions):
    """モデルごとに全設問をまとめて実行し、質問ID -> モデル -> 結果 の形で返す。"""
    detail = {q["id"]: {} for q in questions}
    for model in models:
        print(f"\n### {model} を実行中 ({len(questions)} 問) ###")
        for q in questions:
            try:
                text, elapsed, usage = client.ask(model, q)
                picked = extract_answer(text)
                ok = picked is not None and picked == str(q["answer"])
                detail[q["id"]][model] = {
                    "picked": picked, "ok": ok, "elapsed": elapsed,
                    "tokens": usage.get("completion_tokens"), "raw": text,
                }
                mark = "○" if ok else ("?" if picked is None else "×")
                print(f"  Q{q['id']:<3} {mark}  回答={picked!s:>4} 正解={q['answer']}"
                      f"  ({elapsed:5.1f}s, {usage.get('completion_tokens', '?')}tok)")
                if picked is None:
                    print(f"       ⚠ 回答形式を検出できず。末尾: ...{text[-150:]!r}")
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", "replace")[:200]
                print(f"  Q{q['id']:<3} HTTPエラー {e.code}: {err}")
                detail[q["id"]][model] = {"picked": None, "ok": False, "elapsed": None,
                                           "tokens": None, "raw": None, "error": err}
            except urllib.error.URLError as e:
                print(f"  Q{q['id']:<3} 接続エラー: {e.reason}")
                detail[q["id"]][model] = {"picked": None, "ok": False, "elapsed": None,
                                           "tokens": None, "raw": None, "error": str(e.reason)}
    return detail


def summarize(models, questions, detail):
    print("\n" + "=" * 66)
    print("設問ごとの比較")
    print("=" * 66)
    header = "Q   正解 " + "".join(f"{m:>18}" for m in models)
    print(header)
    for q in questions:
        row = f"{q['id']:<3} {q['answer']:<4} "
        for model in models:
            r = detail[q["id"]].get(model, {})
            mark = "○" if r.get("ok") else ("?" if r.get("picked") is None else "×")
            row += f"{mark} {str(r.get('picked')):>4}({r.get('elapsed') or 0:4.1f}s) "
        print(row)

    print("\n" + "=" * 66)
    print("サマリ")
    print("=" * 66)
    for model in models:
        results = [detail[q["id"]][model] for q in questions if model in detail[q["id"]]]
        total = len(results)
        correct = sum(1 for r in results if r.get("ok"))
        timed = [r["elapsed"] for r in results if r.get("elapsed") is not None]
        toked = [r["tokens"] for r in results if r.get("tokens") is not None]
        acc = correct / total * 100 if total else 0.0
        avg_t = sum(timed) / len(timed) if timed else 0.0
        avg_tok = sum(toked) / len(toked) if toked else 0.0
        print(f"{model:14s}  正答率 {correct:2d}/{total:2d} ({acc:5.1f}%)  "
              f"平均 {avg_t:5.1f}s / {avg_tok:6.0f}tok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions", help="設問JSONファイル(例: evals/data/kokugo_sample.json)")
    ap.add_argument("--models", default="quality,quality-next",
                     help="比較するモデルの論理名をカンマ区切りで(既定: quality,quality-next)")
    ap.add_argument("--max-tokens", type=int, default=8192,
                     help="1問あたりの max_tokens(思考込み。既定: 8192)")
    ap.add_argument("--no-think", action="store_true",
                     help="思考を止めて解かせる(速いが読解精度は下がりうる)")
    ap.add_argument("--hub-url", default=os.environ.get("AI_HUB_URL", "http://localhost:20800"))
    args = ap.parse_args()

    key = resolve_key()
    if not key:
        print("AI_HUB_KEY が未設定です(環境変数、または l-llm-containers/.env から自動取得)")
        sys.exit(1)

    with open(args.questions, encoding="utf-8") as f:
        questions = json.load(f)
    for q in questions:
        q["answer"] = str(q["answer"])

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    client = Client(args.hub_url, key, args.max_tokens, think=(False if args.no_think else None))

    detail = run(client, models, questions)
    summarize(models, questions, detail)


if __name__ == "__main__":
    main()
