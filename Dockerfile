# Two stages: ObsPy ships no linux/arm64 wheel, so on that platform it compiles
# from source and needs a C toolchain that the runtime image should not carry.
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv resolves and installs from the committed lock file, so an image rebuilt
# months from now gets the exact dependency set this release was tested with.
# Runtime and dev groups are both installed: the image is also the test runner
# (run_tests.sh executes pytest and ruff inside it).
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-cache


FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /app /app
COPY tests/ tests/

# Non-root user; the server needs no privileges and writes nothing.
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

ENV PATH="/app/.venv/bin:${PATH}"

# stdio by default (`docker run -i`); Streamable HTTP with
# `-e MCP_TRANSPORT=streamable-http -p 8000:8000` (endpoint /mcp).
EXPOSE 8000
CMD ["python", "-m", "fdsnws_station_server.server"]
