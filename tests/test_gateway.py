"""Tests for the teaching LLM gateway and cost report (Chapter 17). No network needed."""
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
from openai import OpenAI

from minicode.cost_report import markdown, summarise
from minicode.gateway import Gateway, make_handler

CONFIG = {
    "models": {
        "strong": {"upstream": "https://strong.example/v1", "model": "big-1",
                   "key_env": "STRONG_KEY", "price_in": 3.0, "price_out": 15.0},
        "cheap": {"upstream": "https://cheap.example/v1", "model": "small-1",
                  "key_env": "CHEAP_KEY", "price_in": 0.2, "price_out": 0.8},
    },
    "routes": {"review": ["strong", "cheap"], "default": ["cheap", "strong"]},
    "keys": {"team-a-key": "team-a"},
    "budgets": {"team-a": 1.0},
    "cache": {"tasks": ["evaluate"]},
}


def upstream(down=()):
    """A fake provider: answers with usage; hosts in `down` return 503."""
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host in down:
            return httpx.Response(503, json={"error": "overloaded"})
        model = json.loads(request.content)["model"]
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": f"hi from {model}"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                      "total_tokens": 1100}})
    return httpx.MockTransport(handle), calls


def ask(gw, task="chat", key="team-a-key", feature="campushub"):
    headers = {"authorization": f"Bearer {key}", "x-task": task, "x-feature": feature}
    return gw.handle(headers, {"model": "auto", "messages": [{"role": "user", "content": "hi"}]})


def test_routes_by_task_and_falls_back(tmp_path):
    transport, calls = upstream(down={"strong.example"})
    gw = Gateway(CONFIG, tmp_path / "log.jsonl", transport)
    status, data = ask(gw, task="chat")
    assert status == 200 and data["model"] == "small-1"           # default route: cheap
    status, data = ask(gw, task="review")
    assert status == 200 and data["model"] == "small-1"           # strong down -> cheap
    assert calls == ["cheap.example", "strong.example", "cheap.example"]
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert log[1]["fallbacks"] == ["strong:503"] and log[1]["cost"] == 0.00028


def test_keys_budgets_and_cache(tmp_path):
    transport, calls = upstream()
    gw = Gateway(CONFIG, tmp_path / "log.jsonl", transport)
    assert ask(gw, key="stolen")[0] == 401
    for _ in range(2):
        assert ask(gw, task="evaluate")[0] == 200
    assert len(calls) == 1                                          # second was cached
    gw.spent[(__import__("time").strftime("%Y-%m-%d"), "team-a")] = 1.0
    status, data = ask(gw)
    assert status == 429 and "budget" in data["error"]["message"]


def test_openai_client_through_the_gateway_and_report(tmp_path):
    transport, _ = upstream()
    gw = Gateway(CONFIG, tmp_path / "log.jsonl", transport)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gw))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = OpenAI(base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    api_key="team-a-key",
                    default_headers={"X-Task": "review", "X-Feature": "registrations"})
    reply = client.chat.completions.create(model="auto",
                                           messages=[{"role": "user", "content": "hi"}])
    server.shutdown()
    assert reply.choices[0].message.content == "hi from big-1"
    entries = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    rows = summarise(entries, "feature")
    assert rows[0]["name"] == "registrations" and rows[0]["cost"] == 0.0045
    assert "| registrations | 1 | 1,100 | 0.0045 | 100% |" in markdown(entries, ["feature"])
