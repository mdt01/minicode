"""minicode v0.15: a cost dashboard from the gateway's log.

    python -m minicode.cost_report gateway-log.jsonl                  # Markdown tables
    python -m minicode.cost_report gateway-log.jsonl --by feature,task --html cost.html

Each table groups requests by one field (team, feature, task or model) and shows the
number of requests, tokens, cost, cache hits, requests that needed a fallback, errors
and the median latency.
"""
import argparse
import html
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ("team", "feature", "task", "model")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def summarise(entries: list[dict], field: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e.get(field, "?")].append(e)
    rows = []
    for name, items in groups.items():
        latencies = [e["latency_ms"] for e in items if e["latency_ms"]]
        rows.append({"name": name, "requests": len(items),
                     "tokens": sum(e["tokens_in"] + e["tokens_out"] for e in items),
                     "cost": sum(e["cost"] for e in items),
                     "cached": sum(e["cached"] for e in items),
                     "fallbacks": sum(bool(e["fallbacks"]) for e in items),
                     "errors": sum(e["status"] != 200 for e in items),
                     "p50_ms": int(statistics.median(latencies)) if latencies else 0})
    return sorted(rows, key=lambda r: -r["cost"])


def markdown(entries: list[dict], fields: list[str]) -> str:
    total = sum(e["cost"] for e in entries)
    out = [f"# LLM cost report\n\n{len(entries)} requests, ${total:.4f} in total.\n"]
    for field in fields:
        out.append(f"\n## By {field}\n\n| {field} | requests | tokens | cost ($) | share | "
                   "cached | fallbacks | errors | p50 ms |\n|---|---|---|---|---|---|---|---|---|")
        for r in summarise(entries, field):
            share = r["cost"] / total if total else 0
            out.append(f"| {r['name']} | {r['requests']} | {r['tokens']:,} | {r['cost']:.4f} | "
                       f"{share:.0%} | {r['cached']} | {r['fallbacks']} | {r['errors']} | "
                       f"{r['p50_ms']} |")
    return "\n".join(out) + "\n"


def to_html(entries: list[dict], fields: list[str]) -> str:
    total = sum(e["cost"] for e in entries) or 1e-9
    sections = []
    for field in fields:
        rows = "".join(
            f"<tr><td>{html.escape(str(r['name']))}</td><td>{r['requests']}</td>"
            f"<td>{r['tokens']:,}</td><td>{r['cost']:.4f}</td><td><div class='bar' "
            f"style='width:{max(2, int(200 * r['cost'] / total))}px'></div></td>"
            f"<td>{r['fallbacks']}</td><td>{r['errors']}</td></tr>"
            for r in summarise(entries, field))
        sections.append(f"<h2>By {field}</h2><table><tr><th>{field}</th><th>requests</th>"
                        f"<th>tokens</th><th>cost ($)</th><th>share</th><th>fallbacks</th>"
                        f"<th>errors</th></tr>{rows}</table>")
    return ("<!doctype html><meta charset='utf-8'><title>LLM cost</title><style>"
            "body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
            "td,th{padding:4px 10px;border-bottom:1px solid #ddd;text-align:left}"
            ".bar{height:12px;background:#1f7a72}</style>"
            f"<h1>LLM cost report</h1><p>{len(entries)} requests, "
            f"${sum(e['cost'] for e in entries):.4f} in total.</p>" + "".join(sections))


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode.cost_report")
    parser.add_argument("log")
    parser.add_argument("--by", default="team,feature,task,model")
    parser.add_argument("--html", help="also write an HTML dashboard to this file")
    args = parser.parse_args()
    entries = load(Path(args.log))
    fields = [f for f in args.by.split(",") if f in FIELDS]
    print(markdown(entries, fields))
    if args.html:
        Path(args.html).write_text(to_html(entries, fields), encoding="utf-8")


if __name__ == "__main__":
    main()
