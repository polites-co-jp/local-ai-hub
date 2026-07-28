#!/usr/bin/env python3
"""ai-hub チャット動作確認アプリ — 極小プロキシ。

- 静的 index.html を配信
- POST /api/chat: ブラウザから受けた全履歴を gateway(LiteLLM)へ中継し SSE を素通し
- master key はサーバ側だけが保持(ブラウザに渡さない)

標準ライブラリのみ。追加 pip 依存なし。
"""
import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATEWAY = os.environ.get("GATEWAY_URL", "http://gateway:4000").rstrip("/")
KEY = os.environ.get("LITELLM_MASTER_KEY", "")
DEFAULT_MODEL = os.environ.get("CHAT_MODEL", "quality")
PORT = int(os.environ.get("PORT", "8000"))
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 簡潔なログ
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(500, "index.html not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/models":
            # モデル選択肢はカタログAPIから取る。設定に書いてあっても実体が未 pull の
            # ものは載らないため、UI に「選べるのに 500 になる」項目が出ない。
            req = urllib.request.Request(
                GATEWAY + "/v1/catalog",
                headers={"Authorization": "Bearer " + KEY},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read()
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req_body = json.loads(self.rfile.read(length) or b"{}")
            messages = req_body.get("messages", [])
            model = req_body.get("model") or DEFAULT_MODEL
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, "invalid JSON")
            return

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": 4096,
        }).encode("utf-8")

        upstream = urllib.request.Request(
            GATEWAY + "/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": "Bearer " + KEY,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(upstream, timeout=600)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": detail}).encode("utf-8"))
            return
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e.reason)}).encode("utf-8"))
            return

        # gateway の SSE をそのままブラウザへ素通し
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for chunk in resp:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # ブラウザが切断
        finally:
            resp.close()


def main():
    print("chat app listening on :%d  (gateway=%s, default model=%s)"
          % (PORT, GATEWAY, DEFAULT_MODEL), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
