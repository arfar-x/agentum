#!/usr/bin/env python3
"""A stub OpenAI-compatible model for the identity end-to-end run (quickstart step 2).

It answers /v1/models and /v1/chat/completions with a fixed reply, and records the
`Authorization` header of every request (only a short fingerprint, never the key) so the
run can check which key the gateway sent upstream. `GET /_seen` returns the records.

    python3 scripts/stub-model.py [--port 9100] [--bind 127.0.0.1]
"""
import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN: list[dict] = []


def fingerprint(header: str) -> str:
    token = header.removeprefix("Bearer ").strip()
    return hashlib.sha256(token.encode()).hexdigest()[:12] if token else ""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self) -> None:
        SEEN.append({"path": self.path, "key_sha256_12": fingerprint(self.headers.get("Authorization", ""))})

    def do_GET(self):  # noqa: N802
        if self.path == "/_seen":
            return self._send(200, {"requests": SEEN})
        self._record()
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._record()
        if self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(200, {
                "id": "stub-1", "object": "chat.completion", "model": "stub-model",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "stub reply"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            })
        self._send(404, {"error": {"message": "not found"}})

    def log_message(self, *args):  # keep the terminal quiet
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args()
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
