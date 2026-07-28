#!/usr/bin/env python3
"""ai-hub モデルカタログ API — 「実際に呼べるモデル」だけを返す。

LiteLLM の /v1/models は設定ファイルに書かれた論理名を返すだけで、実体(ollama の
モデル)が pull されているかは見ていない。そのため未 pull のモデルが一覧に載り、
呼ぶと 500 になる。ここでは 2 つの権威情報を突合して「一覧 = 実行可能」を保証する:

  1. gateway /model/info  … 論理名 → 実体 のルーティング定義(litellm.config.yaml 由来)
  2. ollama /api/tags     … 実際に pull 済みの実体

外部公開は gateway(:20800) の pass-through 経由。認証はゲートウェイ任せにせず
本サービス自身でも master key を検証する(pass-through の auth は Enterprise 機能で
OSS 版では効かないため、ここが実質的な関門になる)。

標準ライブラリのみ。追加 pip 依存なし。
"""
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATEWAY = os.environ.get("GATEWAY_URL", "http://gateway:4000").rstrip("/")
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
KEY = os.environ.get("LITELLM_MASTER_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "30"))

# kind → 呼び出しに使うエンドポイント。model_info.endpoint で上書きできる。
KIND_ENDPOINT = {
    "chat": "/v1/chat/completions",
    "embedding": "/v1/embeddings",
}

# litellm.config.yaml の model_info から外部へ出すキー(ホワイトリスト)。
# LiteLLM は既知モデルに対して model_info へ料金表や能力フラグを大量に自動補完する
# (embed が ollama/bge-m3 として認識されるとこれが起きる)。素通しすると推測値混じりの
# ノイズがカタログに載るため、こちらで定義したキーだけを通す。
META_KEYS = ("purpose", "context_length", "dimensions", "thinking", "notes")


def _get_json(url, key=None):
    req = urllib.request.Request(url, method="GET")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_backend(litellm_model):
    """litellm の 'ollama_chat/qwen3:14b' → ollama 上の 'qwen3:14b'。

    ollama は tag 省略時に ':latest' を補うため、比較用に同じ正規化をかける。
    """
    if not litellm_model:
        return None
    name = litellm_model.split("/", 1)[1] if "/" in litellm_model else litellm_model
    return name if ":" in name else name + ":latest"


def build_catalog(include_uninstalled=False):
    """ルーティング定義と pull 済み実体を突合してカタログを組み立てる。"""
    info = _get_json(GATEWAY + "/model/info", KEY)
    tags = _get_json(OLLAMA + "/api/tags")

    installed = {}
    for m in tags.get("models", []):
        name = m.get("name")
        if name:
            installed[name] = m

    entries = []
    for row in info.get("data", []):
        logical = row.get("model_name")
        params = row.get("litellm_params") or {}
        raw_meta = row.get("model_info") or {}
        meta = {k: raw_meta[k] for k in META_KEYS if raw_meta.get(k) is not None}

        backend = _normalize_backend(params.get("model"))
        hit = installed.get(backend)
        kind = raw_meta.get("kind") or "chat"

        entry = {
            "id": logical,
            "object": "model",
            "kind": kind,
            "endpoint": raw_meta.get("endpoint") or KIND_ENDPOINT.get(kind, "/v1/chat/completions"),
            "installed": hit is not None,
            # 参考情報。ハブ側で差し替えるためクライアントは依存しないこと。
            "backend": backend,
        }
        if hit is not None:
            entry["backend_size_bytes"] = hit.get("size")
        else:
            entry["unavailable_reason"] = (
                "backend model '%s' is not pulled on the ollama runtime" % backend
            )
        entry.update(meta)  # purpose / context_length / thinking / dimensions など
        entries.append(entry)

    if not include_uninstalled:
        entries = [e for e in entries if e["installed"]]
    entries.sort(key=lambda e: (e["kind"] != "chat", e["id"]))
    return {"object": "list", "data": entries}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not KEY:  # 鍵未設定なら誰も通さない(fail closed)
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):].strip(), KEY)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        if path not in ("/catalog", "/v1/catalog"):
            self._send(404, {"error": {"message": "not found", "code": 404}})
            return
        if not self._authorized():
            self._send(401, {"error": {
                "message": "missing or invalid API key. Send 'Authorization: Bearer <key>'.",
                "code": 401,
            }})
            return

        include_uninstalled = any(
            p in ("all=1", "all=true", "include_uninstalled=1") for p in query.split("&")
        )
        try:
            self._send(200, build_catalog(include_uninstalled))
        except urllib.error.URLError as e:
            # 上流(gateway / ollama)が落ちている場合は 502 で理由を返す
            self._send(502, {"error": {
                "message": "upstream unavailable: %s" % (getattr(e, "reason", e),),
                "code": 502,
            }})
        except (ValueError, KeyError) as e:
            self._send(500, {"error": {"message": "catalog build failed: %s" % (e,), "code": 500}})


def main():
    print("catalog api listening on :%d (gateway=%s, ollama=%s)" % (PORT, GATEWAY, OLLAMA),
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
