"""minicode v0.3: record every request and response, so you can see what the model sees."""
import json
import time
from pathlib import Path


class TracingClient:
    """Wraps an OpenAI client and appends each call to a JSON Lines file."""

    def __init__(self, client, path: str | Path):
        self.client = client
        self.path = Path(path)
        self.chat = self
        self.completions = self

    def create(self, **request):
        started = time.time()
        response = self.client.chat.completions.create(**request)
        record = {"time": started, "seconds": round(time.time() - started, 2),
                  "request": request, "response": response.model_dump()}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return response
