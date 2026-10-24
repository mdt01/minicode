"""minicode v0.15: a teaching-size LLM gateway - one endpoint, many models.

    python -m minicode.gateway --config gateway.toml --port 8787
    # then point any OpenAI-compatible client (minicode included) at it:
    LLM_BASE_URL=http://127.0.0.1:8787/v1 LLM_API_KEY=<team key> LLM_MODEL=auto

What it does for every POST /v1/chat/completions:
  1. Authenticate the caller with a *virtual key* -> team. Real provider keys never
     leave the gateway.
  2. Pick a route: X-Task header (chat, review, loop, ...) -> list of model aliases.
     The first alias is the primary; the others are fallbacks.
  3. Enforce daily budgets per team, and per feature ("feature:<name>") if configured.
  4. Serve identical requests from a small cache, if the route allows it.
  5. Call the upstream; on 429, 5xx or a network error, try the next alias.
  6. Log one JSON line: team, feature, task, model, tokens, cost, latency, fallbacks.

Production gateways (open source and commercial) add streaming, retries with backoff,
quotas per key, guardrails and dashboards; this one fits in a chapter. Not for
production use: no streaming, no TLS, single process.
"""
import argparse
import hashlib
import json
import os
import threading
import time
import tomllib
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

RETRYABLE = {429, 500, 502, 503, 504}


class Gateway:
    def __init__(self, config: dict, log_file: Path, transport=None):
        self.models = config["models"]                       # alias -> settings
        self.routes = config.get("routes", {"default": list(self.models)})
        self.keys = config.get("keys", {})                   # virtual key -> team
        self.budgets = config.get("budgets", {})   # "team" or "feature:<name>" -> $/day
        self.cache_tasks = set(config.get("cache", {}).get("tasks", []))
        self.log_file = log_file
        self.http = httpx.Client(timeout=120, transport=transport)
        self.cache: OrderedDict[str, dict] = OrderedDict()
        self.spent: dict[tuple[str, str], float] = {}        # (day, team|feature) -> $
        self.lock = threading.Lock()

    # ---- the request path -----------------------------------------------------------
    def handle(self, headers: dict, body: dict) -> tuple[int, dict]:
        team = self.keys.get(headers.get("authorization", "").removeprefix("Bearer ").strip())
        if team is None:
            return 401, error("unknown gateway key")
        if body.get("stream"):
            return 400, error("streaming is not supported by the minicode gateway")
        task = headers.get("x-task", "default")
        feature = headers.get("x-feature", "default")
        scopes = (team, f"feature:{feature}")
        for scope in scopes:
            limit = self.budgets.get(scope)
            if limit is not None and self.today(scope) >= limit:
                self.log(team, feature, task, "-", 0, 0, 0.0, 0, 429, [], False)
                return 429, error(f"daily budget of ${limit:.2f} for '{scope}' is used up")
        route = self.route(body.get("model", "auto"), task)
        key = self.cache_key(body) if task in self.cache_tasks else None
        if key and key in self.cache:
            self.log(team, feature, task, "cache", 0, 0, 0.0, 0, 200, [], True)
            return 200, self.cache[key]
        tried = []
        for alias in route:
            model = self.models[alias]
            started = time.perf_counter()
            try:
                response = self.http.post(
                    f"{model['upstream'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {os.getenv(model['key_env'], '')}"},
                    json={**body, "model": model["model"]})
                status = response.status_code
            except httpx.HTTPError:
                status = 599
            latency = int((time.perf_counter() - started) * 1000)
            if status in RETRYABLE or status == 599:
                tried.append(f"{alias}:{status}")
                continue                                     # fall back to the next model
            data = response.json()
            usage = data.get("usage") or {}
            tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            cost = (tin * model.get("price_in", 0) + tout * model.get("price_out", 0)) / 1e6
            if status == 200:
                with self.lock:
                    for scope in scopes:
                        k = (time.strftime("%Y-%m-%d"), scope)
                        self.spent[k] = self.spent.get(k, 0.0) + cost
                    if key:
                        self.cache[key] = data
                        self.cache.move_to_end(key)
                        while len(self.cache) > 500:
                            self.cache.popitem(last=False)
            self.log(team, feature, task, alias, tin, tout, cost, latency, status, tried, False)
            return status, data
        self.log(team, feature, task, "-", 0, 0, 0.0, 0, 503, tried, False)
        return 503, error(f"all models failed: {', '.join(tried)}")

    def route(self, requested: str, task: str) -> list[str]:
        if requested in self.models:                         # caller asked for an alias
            return [requested]
        return self.routes.get(task) or self.routes.get("default") or list(self.models)

    def today(self, scope: str) -> float:
        return self.spent.get((time.strftime("%Y-%m-%d"), scope), 0.0)

    @staticmethod
    def cache_key(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def log(self, team, feature, task, alias, tin, tout, cost, latency, status, tried,
            cached) -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "team": team,
                 "feature": feature, "task": task, "model": alias, "tokens_in": tin,
                 "tokens_out": tout, "cost": round(cost, 6), "latency_ms": latency,
                 "status": status, "fallbacks": tried, "cached": cached}
        with self.lock, self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def error(message: str) -> dict:
    return {"error": {"message": message, "type": "gateway_error"}}


def make_handler(gateway: Gateway):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):                                   # noqa: N802
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self.reply(404, error("only /v1/chat/completions is supported"))
            length = int(self.headers.get("content-length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.reply(400, error("invalid JSON"))
            headers = {k.lower(): v for k, v in self.headers.items()}
            self.reply(*gateway.handle(headers, body))

        def reply(self, status: int, data: dict):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):                        # quiet: we log JSON lines
            pass
    return Handler


def serve(config_file: Path, port: int, log_file: Path) -> ThreadingHTTPServer:
    gateway = Gateway(tomllib.loads(config_file.read_text(encoding="utf-8")), log_file)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(gateway))


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode.gateway")
    parser.add_argument("--config", default="gateway.toml")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--log", default="gateway-log.jsonl")
    args = parser.parse_args()
    server = serve(Path(args.config), args.port, Path(args.log))
    print(f"minicode gateway on http://127.0.0.1:{args.port}/v1  (log: {args.log})")
    server.serve_forever()


if __name__ == "__main__":
    main()
