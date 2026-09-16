#!/usr/bin/env bash
# Complete test suite for the FDSNWS Station MCP server.
#
# Default: build the image, lint, run the offline unit and protocol suites in it.
# Pass --integration to also run the live tests against the advertised
# Datacenters (INGV, EARTHSCOPE, GFZ, ORFEUS; network required).
set -euo pipefail

readonly IMAGE="mcp-fdsnws-station-server"
RUN_INTEGRATION=0
if [[ "${1:-}" == "--integration" ]]; then
    RUN_INTEGRATION=1
fi

echo "FDSNWS Station MCP Server - Test Suite"
echo "======================================"

echo ""
echo "Step 1/4: Building Docker image..."
docker build -t "${IMAGE}" .

echo ""
echo "Step 2/4: Lint (ruff)..."
docker run --rm "${IMAGE}" ruff check src tests
docker run --rm "${IMAGE}" ruff format --check src tests

echo ""
echo "Step 3/4: Offline tests (unit + protocol)..."
docker run --rm "${IMAGE}" pytest -q

if [[ "${RUN_INTEGRATION}" -eq 1 ]]; then
    echo ""
    echo "Step 4/4: Integration tests (live INGV, EARTHSCOPE, GFZ, ORFEUS)..."
    docker run --rm "${IMAGE}" pytest -q -m integration
else
    echo ""
    echo "Step 4/4: Integration tests SKIPPED (pass --integration to run them)."
fi

echo ""
echo "All tests passed."
