# Evaluations

Two scripts, both live and both opt-in. Neither runs in CI.

- `measure_context_cost.py`: what one tool result costs a model, in bytes and
  tokens (tiktoken `o200k_base`), for the same Epochs as raw `format=text`,
  `columns`+`rows`, typed compact JSON and the SDK's indented text block.
- `run_evals.py` with `questions.json`: verifiable questions answered by a model
  through the four tools via an OpenAI-compatible endpoint with native tool calling
  (Ollama's `/v1` works). Tool calls run in-process through `MCPServer.call_tool`,
  so the model reads exactly the text block an MCP client would hand it. Scoring is
  regex containment on the final answer against values established live on the
  date in `questions.json`; re-verify them before trusting a run.

```bash
uv sync --group evals
uv run --group evals python tests/evals/measure_context_cost.py --json cost.json
uv run --group evals python tests/evals/run_evals.py \
    --endpoint http://host:11434/v1 --model qwen3.8:27b --repeats 3 --out tests/evals/results
```

`results/` holds one JSON per run (every answer, every metric). The results used in
the manuscript are committed; new runs add files, never overwrite them.
