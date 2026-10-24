"""The tracing proxy forwards requests unchanged and logs them without secrets."""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.trace_proxy import make_handler


class FakeProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        reply = json.dumps({"echo": json.loads(body), "auth_ok": self.headers["Authorization"] == "Bearer SECRET"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *args):
        pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_proxy_forwards_and_logs(tmp_path):
    provider, provider_url = serve(FakeProvider)
    out = tmp_path / "trace.jsonl"
    proxy, proxy_url = serve(make_handler(provider_url, str(out)))
    request = urllib.request.Request(
        proxy_url + "/v1/messages", data=json.dumps({"model": "m", "messages": []}).encode(),
        headers={"Authorization": "Bearer SECRET", "Content-Type": "application/json"})
    reply = json.loads(urllib.request.urlopen(request).read())
    assert reply == {"echo": {"model": "m", "messages": []}, "auth_ok": True}
    logged = out.read_text()
    assert json.loads(logged)["request"]["model"] == "m"
    assert "SECRET" not in logged              # headers are never logged
    proxy.shutdown()
    provider.shutdown()
