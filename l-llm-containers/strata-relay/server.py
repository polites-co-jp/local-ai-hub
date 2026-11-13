#!/usr/bin/env python3
"""ai-hub Strata 中継 — Strata のネイティブ API をハブの API キーで使えるようにする。

gateway(LiteLLM)の `large` は OpenAI 互換の Chat Completions だけを扱い、Anthropic Messages /
Responses は LiteLLM が自前で変換してしまう。状態取得系(/props /status /slots /metrics /mcp)は
そもそも通らない。この中継は Strata の API をそのまま(変換せずに)出す。

  クライアント → gateway /strata/<path>(pass-through) → この中継 → strata:8080/<path>

- 認証: ハブの master key(Authorization: Bearer か x-api-key)を検証してから、上流には Strata 自身の
  鍵を付け替えて送る。Strata の鍵はハブの外へ出ない。鍵が未設定なら誰も通さない(fail closed)。
- 中継するのは下の ROUTES に書いたものだけ。会話の保存・復元(POST /slots)など、サーバの状態を
  書き換える API は出さない。
- ハブの既定は「思考なし」。リクエストが思考の強さを何も指定していなければ、API ごとの書き方で
  none を補う(gateway の large と同じ振る舞い)。
- ストリーミング(SSE)は届いた分からそのまま流す。クライアントが切断したら上流も閉じる
  (Strata は接続が閉じると生成を止める)。

標準ライブラリのみ。追加 pip 依存なし。
"""
import hmac
import http.client
import json
import os
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = urllib.parse.urlsplit(os.environ.get("STRATA_URL", "http://strata:8080"))
STRATA_KEY = os.environ.get("STRATA_API_KEY", "")
HUB_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
PORT = int(os.environ.get("PORT", "8090"))
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "900"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", str(64 * 1024 * 1024)))  # 画像入りでも十分な上限
# gateway の pass-through が付ける接頭辞。直接呼ばれた場合も同じ経路で受ける。
PREFIX = "/strata"

# (メソッド, パス) → 思考の既定を補う関数(None なら補わない)
def _default_chat(body):
    kw = body.get("chat_template_kwargs") or {}
    if "reasoning_effort" not in body and "reasoning" not in body and "enable_thinking" not in kw:
        body["reasoning_effort"] = "none"


def _default_messages(body):
    if "thinking" not in body and "output_config" not in body:
        body["thinking"] = {"type": "disabled"}


def _default_responses(body):
    if "reasoning" not in body and "reasoning_effort" not in body:
        body["reasoning"] = {"effort": "none"}


ROUTES = {
    ("POST", "/v1/chat/completions"): _default_chat,
    ("POST", "/v1/messages"): _default_messages,
    ("POST", "/v1/messages/count_tokens"): None,
    ("POST", "/v1/responses"): _default_responses,
    ("GET", "/v1/models"): None,
    ("GET", "/props"): None,
    ("GET", "/status"): None,
    ("GET", "/slots"): None,
    ("GET", "/metrics"): None,
    ("GET", "/metrics/prometheus"): None,
    ("GET", "/mcp-servers"): None,
    ("GET", "/health"): None,
}

# 中継側の名前 → Strata 上のパス。LiteLLM は /{name}/mcp を自分の MCP ルートとして横取りするため、
# Strata の GET /mcp(登録済み MCP サーバとツールの一覧)は /strata/mcp-servers として出す。
UPSTREAM_PATH = {"/mcp-servers": "/mcp"}

# 上流へそのまま渡すリクエストヘッダ(認証系は渡さず付け替える)
FORWARD_REQ_HEADERS = ("content-type", "accept", "anthropic-version", "anthropic-beta")
# クライアントへ返すレスポンスヘッダ(長さ・転送方式はこちらで決める)
DROP_RESP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "server", "date"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    # ---- 共通 ----
    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        self._json(code, {"error": {"message": message, "type": "strata_relay_error", "code": code}})

    def _authorized(self):
        if not HUB_KEY or not STRATA_KEY:
            return False
        token = ""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[len("Bearer "):].strip()
        elif self.headers.get("x-api-key"):
            token = self.headers.get("x-api-key").strip()  # Anthropic SDK はこちらで送る
        return bool(token) and hmac.compare_digest(token, HUB_KEY)

    def _route(self):
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        if path.startswith(PREFIX + "/") or path == PREFIX:
            path = path[len(PREFIX):] or "/"
        path = path.rstrip("/") or "/"
        return path, split.query

    # ---- 入口 ----
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        path, query = self._route()
        if method == "GET" and path == "/healthz":  # 中継自身の死活(認証不要)
            self._json(200, {"status": "ok"})
            return
        if (method, path) not in ROUTES:
            allowed = sorted("%s %s%s" % (m, PREFIX, p) for m, p in ROUTES)
            self._json(404, {"error": {"message": "not relayed: %s %s" % (method, path),
                                       "type": "strata_relay_error", "code": 404, "allowed": allowed}})
            return
        if not self._authorized():
            self._error(401, "missing or invalid API key. Send 'Authorization: Bearer <key>' (or 'x-api-key').")
            return

        body = None
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._error(413 if length > MAX_BODY else 400, "invalid or too large request body")
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._error(400, "request body is not JSON: %s" % e)
                return
            if not isinstance(payload, dict):
                self._error(400, "request body must be a JSON object")
                return
            fill = ROUTES[(method, path)]
            if fill:
                fill(payload)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        upstream = UPSTREAM_PATH.get(path, path)
        self._relay(method, upstream + ("?" + query if query else ""), body)

    def _relay(self, method, target, body):
        headers = {"Authorization": "Bearer " + STRATA_KEY, "x-api-key": STRATA_KEY}
        for name in FORWARD_REQ_HEADERS:
            value = self.headers.get(name)
            if value:
                headers[name] = value
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=TIMEOUT)
        try:
            try:
                conn.request(method, target, body=body, headers=headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as e:
                self._error(502, "strata unavailable: %s" % (e,))
                return

            self.send_response(resp.status)
            for name, value in resp.getheaders():
                if name.lower() not in DROP_RESP_HEADERS:
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass  # クライアントが切断 → finally で上流も閉じ、Strata が生成を止める
        finally:
            conn.close()


def main():
    print("strata relay listening on :%d (upstream=%s://%s:%s)"
          % (PORT, UPSTREAM.scheme, UPSTREAM.hostname, UPSTREAM.port), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
