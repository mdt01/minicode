"""Build tool definitions for the model from ordinary Python functions."""
import inspect
from typing import Callable

PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


def tool_schema(name: str, fn: Callable) -> dict:
    """Describe fn as an OpenAI-compatible tool, using its signature and docstring."""
    sig = inspect.signature(fn)
    properties, required = {}, []
    for pname, param in sig.parameters.items():
        properties[pname] = {"type": PY_TO_JSON.get(param.annotation, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": inspect.getdoc(fn) or "",
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def all_schemas(tools: dict[str, Callable]) -> list[dict]:
    return [tool_schema(name, fn) for name, fn in tools.items()]
