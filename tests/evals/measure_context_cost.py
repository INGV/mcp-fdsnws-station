#!/usr/bin/env python3
"""Measure what one tool result costs a model's context, in bytes and tokens.

For each case the same Datacenter answer is expressed four ways: the raw
`format=text` body as the service returns it, the same Epochs as a
`columns`+`rows` table (the shape the event server uses), the typed Epoch
objects this server returns as `structuredContent` (compact JSON), and the
indented text block the SDK renders next to it, which is what an MCP client
hands to the model. Tokens are counted with tiktoken's `o200k_base` encoding,
one declared tokenizer, so the ratios between representations are comparable.
Absolute counts are not a deployed model's: qwen3.8 counts 1.24 to 1.61 times
as many tokens for the same text, depending on its shape (see
`calibrate_density.py`).

Live against the Datacenters, so the numbers carry a date. Run:

    uv run --group evals python tests/evals/measure_context_cost.py [--json out.json]
"""

import argparse
import asyncio
import json
from datetime import date

import requests
import tiktoken
from pydantic_core import to_json

from fdsnws_station_server import server

ENC = tiktoken.get_encoding("o200k_base")

# (label, tool, arguments): small, medium and large cardinalities per level,
# on INGV, plus one channel case on another Datacenter. The tool functions are
# called directly, so `limit` 500 bypasses the published cap on purpose: the
# large cases show what the cap is there to prevent.
NET, STA, CHA = (
    server.fdsnws_station_query_networks,
    server.fdsnws_station_query_stations,
    server.fdsnws_station_query_channels,
)
CASES = [
    ("network, INGV, all", NET, {"limit": 500}),
    ("station, IV.A*", STA, {"network": "IV", "station": "A*", "limit": 500}),
    ("station, IV, all", STA, {"network": "IV", "limit": 500}),
    ("channel, IV.CAMP.HHZ", CHA, {"network": "IV", "station": "CAMP", "channel": "HHZ"}),
    ("channel, IV.ACER, all", CHA, {"network": "IV", "station": "ACER", "limit": 500}),
    ("channel, IV, default page", CHA, {"network": "IV"}),
    ("channel, IV, 500 epochs", CHA, {"network": "IV", "limit": 500}),
    (
        "channel, GE.APE, all (GFZ)",
        CHA,
        {"network": "GE", "station": "APE", "limit": 500, "datacenter": "GFZ"},
    ),
]
RESPONSE_CASES = [
    (
        "response, IV.CAMP..HHZ, open epoch",
        {"network": "IV", "station": "CAMP", "channel": "HHZ", "starttime": "2025-08-13"},
    ),
    ("response, IV.ACER..HHZ, all epochs", {"network": "IV", "station": "ACER", "channel": "HHZ"}),
]


def count(text: str) -> int:
    return len(ENC.encode(text))


async def one(label, tool, args):
    result = await tool(**args)
    sc = result.model_dump(mode="json")
    raw = requests.get(result.api_url, timeout=60).text
    rows = {k: v for k, v in sc.items() if k != "epochs"}
    rows["columns"] = list(sc["epochs"][0].keys()) if sc["epochs"] else []
    rows["rows"] = [list(e.values()) for e in sc["epochs"]]
    typed = to_json(sc).decode()
    # What the SDK puts in the TextContent block next to structuredContent.
    text_block = to_json(sc, indent=2).decode()
    n = sc["pagination"]["returned_count"]
    total = sc["pagination"]["total_count"]
    # The raw body is the whole result, not the page: scale it to the page so
    # the four columns describe the same Epochs.
    raw_lines = [ln for ln in raw.splitlines() if ln and not ln.startswith("#")]
    raw_page = "\n".join(raw.splitlines()[:1] + raw_lines[:n])
    return {
        "case": label,
        "epochs": n,
        "total": total,
        "raw_text": (len(raw_page.encode()), count(raw_page)),
        "columns_rows": (len(to_json(rows)), count(to_json(rows).decode())),
        "typed_compact": (len(typed.encode()), count(typed)),
        "sdk_text_block": (len(text_block.encode()), count(text_block)),
    }


async def one_response(label, args):
    result = await server.fdsnws_station_get_response(**args)
    sc = result.model_dump(mode="json")
    raw = requests.get(result.api_url, timeout=60).text
    typed = to_json(sc).decode()
    text_block = to_json(sc, indent=2).decode()
    return {
        "case": label,
        "epochs": result.channel_epochs_count,
        "total": result.channel_epochs_count,
        "raw_text": (len(raw.encode()), count(raw)),
        "columns_rows": None,
        "typed_compact": (len(typed.encode()), count(typed)),
        "sdk_text_block": (len(text_block.encode()), count(text_block)),
    }


async def main(out):
    results = [await one(*c) for c in CASES] + [await one_response(*c) for c in RESPONSE_CASES]
    print(f"Measured {date.today().isoformat()}, tokens = tiktoken o200k_base\n")
    print(
        "| Case | Epochs (of total) | raw text B / tok | columns+rows B / tok "
        "| typed compact B / tok | SDK text block B / tok |"
    )
    print("|---|---:|---:|---:|---:|---:|")

    def cell(v):
        return "n/a" if v is None else f"{v[0]} / {v[1]}"

    for r in results:
        print(
            f"| {r['case']} | {r['epochs']} ({r['total']}) | {cell(r['raw_text'])} "
            f"| {cell(r['columns_rows'])} | {cell(r['typed_compact'])} "
            f"| {cell(r['sdk_text_block'])} |"
        )
    if out:
        json.dump(
            {"date": date.today().isoformat(), "tokenizer": "o200k_base", "results": results},
            open(out, "w"),
            indent=1,
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    asyncio.run(main(a.json))
