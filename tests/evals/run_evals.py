#!/usr/bin/env python3
"""Task-based evaluation: can a language model answer verifiable station-metadata
questions through the four tools, and what does it cost?

The harness is the thinnest possible MCP client. It advertises the tools exactly as
`tools/list` describes them (name, description, input schema), sends the question to
an OpenAI-compatible chat endpoint with native tool calling, executes every tool call
in-process through `MCPServer.call_tool` (the same conversion the wire uses, so the
model reads the SDK's text block verbatim), feeds the text back as a tool message and
loops until the model answers or the call budget runs out. Nothing is retried or
corrected on the model's behalf.

Each question carries the values a correct answer must contain, established from
the live Datacenter on the day the questions were written (see questions.json).
Scoring is string and regex containment, case-insensitive, on the final answer.

    uv run --group evals python tests/evals/run_evals.py \
        --endpoint http://host:11434/v1 --model qwen3.8:27b --repeats 3 --out results/

Recorded per run: correct, number of tool calls, calls rejected by validation, tool
result tokens (tiktoken o200k_base), prompt tokens as reported by the endpoint
(the model's own tokenizer), whether a second page or a larger limit was requested,
and wall time. Never run in CI: it needs a model endpoint and the live Datacenters.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
import tiktoken
from mcp.server.mcpserver.exceptions import ToolError

from fdsnws_station_server import server

sys.path.insert(0, str(Path(__file__).parent))
from scoring import score  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
SYSTEM = (
    "You answer questions about seismic station metadata using the tools. "
    "Report only values returned by the tools; if a tool reports no data or an "
    "error, say so. Answer concisely."
)
MAX_CALLS = 8


async def tool_specs() -> list[dict]:
    """OpenAI function specs straight from the server's own tool list."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in await server.mcp.list_tools()
    ]


async def execute(name: str, arguments: dict) -> tuple[str, bool]:
    """Run one tool call the way the SDK would over the wire: the text block on
    success, the ToolError message (as the client sees it) on rejection."""
    try:
        result = await server.mcp.call_tool(name, arguments)
    except ToolError as e:
        return f"Error executing tool {name}: {e}", True
    text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return text, bool(result.is_error)


def chat(endpoint: str, model: str, messages: list, tools: list, timeout: int) -> dict:
    r = requests.post(
        f"{endpoint.rstrip('/')}/chat/completions",
        json={"model": model, "messages": messages, "tools": tools, "temperature": 0},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


async def run_question(q: dict, endpoint: str, model: str, tools: list, timeout: int) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q["question"]}]
    calls, rejected, tool_tokens, prompt_tokens, paged = 0, 0, 0, 0, False
    calls_made: list[dict] = []
    t0 = time.monotonic()
    answer = ""
    error = None
    while True:
        try:
            reply = chat(endpoint, model, messages, tools, timeout)
        except requests.RequestException as e:
            # A model that never finishes (gpt-oss has spun past 900 s on one
            # question) is a failed answer, not a reason to lose the other runs.
            error = f"{type(e).__name__}: {e}"
            break
        prompt_tokens = max(prompt_tokens, reply.get("usage", {}).get("prompt_tokens", 0))
        msg = reply["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls or calls >= MAX_CALLS:
            answer = msg.get("content") or ""
            break
        messages.append(msg)
        for tc in tool_calls:
            calls += 1
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if args.get("offset", 0) or args.get("limit", 50) > 50:
                paged = True
            text, is_error = await execute(fn["name"], args)
            calls_made.append({"tool": fn["name"], "arguments": args, "is_error": is_error})
            rejected += int(is_error)
            tool_tokens += len(ENC.encode(text))
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": text})
    elapsed = time.monotonic() - t0
    missing, forbidden = score(q, answer)
    return {
        "id": q["id"],
        "correct": not missing and not forbidden,
        "missing": missing,
        "forbidden": forbidden,
        "tool_calls": calls,
        "calls": calls_made,
        "rejected_calls": rejected,
        "tool_result_tokens": tool_tokens,
        "prompt_tokens_reported": prompt_tokens,
        "paged": paged,
        "needs_paging": q.get("needs_paging", False),
        "seconds": round(elapsed, 1),
        "answer": answer,
        "error": error,
    }


async def main(a):
    questions = json.loads(Path(a.questions).read_text())
    tools = await tool_specs()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for rep in range(a.repeats):
        for q in questions:
            r = await run_question(q, a.endpoint, a.model, tools, a.timeout)
            r["repeat"] = rep
            runs.append(r)
            flag = "ok " if r["correct"] else "FAIL"
            print(
                f"[{a.model}] rep {rep} {q['id']:<4} {flag} calls={r['tool_calls']} "
                f"rej={r['rejected_calls']} tok={r['tool_result_tokens']} "
                f"prompt={r['prompt_tokens_reported']} {r['seconds']}s",
                file=sys.stderr,
            )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{a.model.replace(':', '_').replace('/', '_')}_{stamp}.json"
    json.dump(
        {
            "model": a.model,
            # The host is deliberately not recorded: results are committed and public.
            "endpoint": a.endpoint_label,
            "date": stamp,
            "system": SYSTEM,
            "max_calls": MAX_CALLS,
            "tokenizer": "o200k_base",
            "runs": runs,
        },
        open(path, "w"),
        indent=1,
    )
    n = len(runs)
    print(
        f"{a.model}: {sum(r['correct'] for r in runs)}/{n} correct, "
        f"mean calls {sum(r['tool_calls'] for r in runs) / n:.2f}, "
        f"rejected {sum(r['rejected_calls'] for r in runs)}, "
        f"mean tool tokens {sum(r['tool_result_tokens'] for r in runs) / n:.0f} -> {path}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True)
    ap.add_argument(
        "--endpoint-label",
        default="OpenAI-compatible endpoint (host not recorded)",
        help="what to record in the results instead of the endpoint URL",
    )
    ap.add_argument("--model", required=True)
    ap.add_argument("--questions", default="tests/evals/questions.json")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="tests/evals/results")
    ap.add_argument("--only", help="run one question id")
    args = ap.parse_args()
    if args.only:
        qs = [q for q in json.loads(Path(args.questions).read_text()) if q["id"] == args.only]
        Path("/tmp/_one.json").write_text(json.dumps(qs))
        args.questions = "/tmp/_one.json"
    asyncio.run(main(args))
