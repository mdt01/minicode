"""A tiny logging proxy for looking inside any coding agent's API traffic.

    python tools/trace_proxy.py --upstream https://api.anthropic.com --out trace.jsonl

Then point your agent at http://127.0.0.1:8080 instead of the provider, e.g.
    ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude              (Claude Code)
    LLM_BASE_URL=http://127.0.0.1:8080/v1 python -m minicode     (minicode)
Each request body and the full response text are appended to the JSON Lines file.
API keys travel in headers, and headers are never written to the file.
Only standard-library modules are used, so it runs anywhere Python 3.10+ runs.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SKIP = {"host", "content-length", "accept-encoding", "connection"}


def make_handler(upstream: str, out: str):
    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
            forward = urllib.request.Request(upstream + self.path, data=body,
                                             headers=headers, method="POST")
            try:
                reply = urllib.request.urlopen(forward, timeout=600)
            except urllib.error.HTTPError as err:
                reply = err                       # pass provider errors through too
            self.send_response(reply.status)
            for key, value in reply.headers.items():
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            received = bytearray()
            while chunk := reply.read1(65536):    # stream chunks as they arrive
                received += chunk
                self.wfile.write(chunk)
                self.wfile.flush()
            self.log_call(body, bytes(received), reply.status)

        def log_call(self, body: bytes, received: bytes, status: int) -> None:
            try:
                request = json.loads(body)
            except ValueError:
                request = {"raw": body.decode("utf-8", "replace")}
            record = {"time": time.time(), "path": self.path, "status": status,
                      "request": request, "response_text": received.decode("utf-8", "replace")}
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            print(f"logged {self.path} -> {status} ({len(body):,} bytes sent)")

        def log_message(self, *args):             # silence the default access log
            pass

    return Proxy


def main() -> None:
    parser = argparse.ArgumentParser(description="Log an agent's API requests.")
    parser.add_argument("--upstream", required=True, help="real API, e.g. https://api.openai.com")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--out", default="trace.jsonl")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(args.upstream.rstrip("/"), args.out))
    print(f"Tracing proxy on http://127.0.0.1:{args.port} -> {args.upstream}")
    server.serve_forever()


if __name__ == "__main__":
    main()
