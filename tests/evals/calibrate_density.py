#!/usr/bin/env python3
"""Measure how many of a model's own tokens one byte of tool result costs.

`measure_context_cost.py` counts tokens with tiktoken, one declared tokenizer.
The model behind a deployment tokenizes differently: on qwen3.8 the reported
prompt tokens of the evaluation runs grow about 1.4 times as fast as the
tiktoken count. A size limit meant to keep a result inside a context window
therefore has to be calibrated on the deployed model, not on tiktoken.

Method: build the exact text block the model reads (the SDK's indented JSON,
obtained through `MCPServer.call_tool`), send it alone as a user message to
Ollama's native `/api/chat`, and read `prompt_eval_count`. Several sizes of the
same result shape give a line; its slope is the density in bytes per token, and
the intercept absorbs the chat template. The density is fitted per shape
because the shapes differ: the response tree is deeply nested, and runs of
indentation tokenize far more cheaply than numbers and codes.

Two traps are avoided on purpose:
- Ollama reuses the KV cache of a shared prompt prefix and does not count the
  reused tokens, so pages of the same query would be undercounted by their
  common head. Every payload therefore starts with a fresh nonce of constant
  length, which moves the shared prefix into the intercept.
- `num_ctx` is never set: a value different from the server's default reloads
  the model, and the host serves other users. A prompt the server truncated is
  flagged and left out of the fit.

    uv run --group evals python tests/evals/calibrate_density.py \\
        --endpoint http://host:11434 --model qwen3.8:27b --out tests/evals/results

The endpoint host is not recorded in the output.
"""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests
import tiktoken

from fdsnws_station_server import server

ENC = tiktoken.get_encoding("o200k_base")

# Ollama answers a prompt longer than its context by dropping tokens and
# reporting at most that many; anything this close is treated as truncated.
TRUNCATION_GUARD = 32_000

# (shape, tool, arguments). Sizes per shape span roughly 1k to 20k tokens, the
# range a size limit for a 32k window has to reason about.
CASES = [
    ("network", "fdsnws_station_query_networks", {"limit": 10}),
    ("network", "fdsnws_station_query_networks", {"limit": 30}),
    ("network", "fdsnws_station_query_networks", {"limit": 66}),
    ("station", "fdsnws_station_query_stations", {"network": "IV", "limit": 10}),
    ("station", "fdsnws_station_query_stations", {"network": "IV", "limit": 50}),
    ("station", "fdsnws_station_query_stations", {"network": "IV", "limit": 100}),
    ("channel", "fdsnws_station_query_channels", {"network": "IV", "limit": 5}),
    ("channel", "fdsnws_station_query_channels", {"network": "IV", "limit": 20}),
    ("channel", "fdsnws_station_query_channels", {"network": "IV", "limit": 40}),
    ("channel", "fdsnws_station_query_channels", {"network": "IV", "limit": 60}),
    (
        "channel",
        "fdsnws_station_query_channels",
        {"network": "GE", "station": "APE", "datacenter": "GFZ"},
    ),
    (
        "response",
        "fdsnws_station_get_response",
        {
            "network": "NL",
            "station": "HGN",
            "channel": "BHZ",
            "starttime": "2024-01-01",
            "endtime": "2024-01-02",
            "datacenter": "ORFEUS",
        },
    ),
    (
        "response",
        "fdsnws_station_get_response",
        {"network": "IV", "station": "CAMP", "channel": "HHZ", "starttime": "2025-08-13"},
    ),
    (
        "response",
        "fdsnws_station_get_response",
        {
            "network": "IV",
            "station": "ACER",
            "channel": "HHZ",
            "starttime": "2022-01-01",
            "endtime": "2022-01-02",
        },
    ),
    (
        "response",
        "fdsnws_station_get_response",
        {"network": "GE", "station": "APE", "channel": "BHZ", "datacenter": "GFZ"},
    ),
    (
        "response",
        "fdsnws_station_get_response",
        {"network": "IV", "station": "ACER", "channel": "HHZ"},
    ),
]


async def text_block(tool: str, arguments: dict) -> str:
    """The text the model reads, through the same conversion the wire uses."""
    result = await server.mcp.call_tool(tool, arguments)
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def prompt_tokens(endpoint: str, model: str, payload: str, timeout: int) -> int:
    nonce = f"[calibration {uuid.uuid4().hex}]\n"
    r = requests.post(
        f"{endpoint.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": nonce + payload}],
            "stream": False,
            # One token is enough: only the prompt evaluation is measured.
            "options": {"num_predict": 1, "temperature": 0},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["prompt_eval_count"]


def fit(points: list[tuple[int, int]]) -> dict | None:
    """Least squares of tokens on bytes; None below two distinct sizes."""
    n = len(points)
    if n < 2:
        return None
    mx = sum(b for b, _ in points) / n
    my = sum(t for _, t in points) / n
    sxx = sum((b - mx) ** 2 for b, _ in points)
    if sxx == 0:
        return None
    sxy = sum((b - mx) * (t - my) for b, t in points)
    syy = sum((t - my) ** 2 for _, t in points)
    slope = sxy / sxx
    return {
        "n": n,
        "bytes_per_token": round(1 / slope, 3),
        "intercept_tokens": round(my - slope * mx, 1),
        "r2": round(sxy * sxy / (sxx * syy), 5) if syy else None,
    }


async def main(endpoint: str, model: str, out: str | None, timeout: int) -> None:
    rows = []
    for shape, tool, arguments in CASES:
        text = await text_block(tool, arguments)
        size = len(text.encode())
        tokens = prompt_tokens(endpoint, model, text, timeout)
        tik = len(ENC.encode(text))
        row = {
            "shape": shape,
            "tool": tool,
            "arguments": arguments,
            "bytes": size,
            "prompt_eval_count": tokens,
            "tiktoken_o200k": tik,
            "truncated": tokens >= TRUNCATION_GUARD,
        }
        rows.append(row)
        print(
            f"{shape:8s} {size:7d} B  native {tokens:6d}  tiktoken {tik:6d}"
            f"{'  TRUNCATED' if row['truncated'] else ''}  {json.dumps(arguments)}",
            flush=True,
        )

    fits = {}
    for shape in dict.fromkeys(r["shape"] for r in rows):
        usable = [r for r in rows if r["shape"] == shape and not r["truncated"]]
        f = fit([(r["bytes"], r["prompt_eval_count"]) for r in usable])
        t = fit([(r["tiktoken_o200k"], r["prompt_eval_count"]) for r in usable])
        if f:
            # Native tokens per tiktoken token: how far Table-style tiktoken
            # counts understate this model.
            f["native_per_tiktoken"] = round(1 / t["bytes_per_token"], 3) if t else None
        fits[shape] = f
        print(f"fit {shape:8s} {f}")

    if out:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = Path(out) / f"calibration_{model.replace(':', '_')}_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "model": model,
                    "date": datetime.now(UTC).isoformat(timespec="seconds"),
                    "ollama_version": requests.get(
                        f"{endpoint.rstrip('/')}/api/version", timeout=10
                    ).json()["version"],
                    "method": "prompt_eval_count of the SDK text block as one user message, "
                    "nonce-prefixed, num_predict 1, server default num_ctx",
                    "fits": fits,
                    "rows": rows,
                },
                indent=1,
            )
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="Ollama base URL, without /v1")
    ap.add_argument("--model", default="qwen3.8:27b")
    ap.add_argument("--out", help="directory for the JSON record")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    asyncio.run(main(a.endpoint, a.model, a.out, a.timeout))
