"""Drive the real server over stdio, as an MCP client would.

Two eras of the protocol are exercised because both kinds of client exist:

- the legacy `initialize` handshake (Claude Desktop and every 1.x client);
- the stateless per-request `_meta` envelope of revision 2026-07-28
  (`server/discover`, then `tools/list` and `tools/call` with the version and
  client capabilities on each request).

No network: the tool call made here fails validation before any request goes
out, which is enough to prove the call path and the error contract. The
per-tool behaviour against a Datacenter is covered by the unit suite (mocked)
and the integration suite (live).
"""

import json
import subprocess
import sys

import pytest

TOOL_NAMES = [
    "fdsnws_station_query_networks",
    "fdsnws_station_query_stations",
    "fdsnws_station_query_channels",
    "fdsnws_station_get_response",
]

MODERN_META = {
    "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1"},
    }
}


class StdioClient:
    """Minimal line-delimited JSON-RPC driver: write, flush, readline."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "fdsnws_station_server.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def recv(self) -> dict:
        line = self.proc.stdout.readline()
        assert line, f"server closed stdout; stderr:\n{self.proc.stderr.read()}"
        return json.loads(line)

    def request(self, id_: int, method: str, params: dict) -> dict:
        self.send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        reply = self.recv()
        assert reply["id"] == id_
        return reply

    def close(self) -> str:
        self.proc.stdin.close()
        self.proc.wait(timeout=15)
        return self.proc.stderr.read()


@pytest.fixture
def server():
    client = StdioClient()
    yield client
    client.close()


def _assert_tool_list(result: dict) -> None:
    assert [t["name"] for t in result["tools"]] == TOOL_NAMES
    for tool in result["tools"]:
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        # A real per-tool output schema, not the SDK's synthetic {"result": ...}
        # wrapper: named model, typed fields, and the in-band error block.
        out = tool["outputSchema"]
        assert out["title"].endswith("Result")
        assert "result" not in out["properties"]
        assert "datacenter" in out["properties"] and "error" in out["properties"]
    query_tools = {t["name"]: t for t in result["tools"] if "query" in t["name"]}
    for name, epoch in [
        ("fdsnws_station_query_networks", "NetworkEpoch"),
        ("fdsnws_station_query_stations", "StationEpoch"),
        ("fdsnws_station_query_channels", "ChannelEpoch"),
    ]:
        out = query_tools[name]["outputSchema"]
        assert out["properties"]["epochs"]["items"] == {"$ref": f"#/$defs/{epoch}"}
        assert "pagination" in out["properties"]
    units = query_tools["fdsnws_station_query_channels"]["outputSchema"]["$defs"]["ChannelEpoch"]
    for field in (
        "elevation_m",
        "depth_m",
        "azimuth_deg",
        "dip_deg",
        "sample_rate_hz",
        "scale_frequency_hz",
        "scale_units",
    ):
        assert field in units["properties"]
    response = next(t for t in result["tools"] if t["name"] == "fdsnws_station_get_response")
    assert set(response["inputSchema"]["required"]) == {"network", "station", "channel"}
    assert "inventory" in response["outputSchema"]["properties"]


def test_legacy_initialize_handshake_then_tools_list(server):
    init = server.request(
        1,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    )["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert init["serverInfo"]["name"] == "fdsnws_station_mcp"
    assert init["serverInfo"]["title"] == "FDSNWS Station"
    assert init["serverInfo"]["websiteUrl"] == "https://github.com/INGV/mcp-fdsnws-station"
    assert "paginated" in init["instructions"]
    assert init["capabilities"]["tools"] == {"listChanged": False}
    server.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    listed = server.request(2, "tools/list", {})["result"]
    _assert_tool_list(listed)


def test_stateless_discover_list_and_call(server):
    discover = server.request(1, "server/discover", {**MODERN_META})["result"]
    assert discover["supportedVersions"] == ["2026-07-28"]
    assert "tools" in discover["capabilities"]
    assert "fdsnws_station_query_networks" in discover["instructions"]
    assert discover["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "fdsnws_station_mcp"

    listed = server.request(2, "tools/list", {**MODERN_META})["result"]
    _assert_tool_list(listed)
    # The one-hour public cache hint of the build-time-constant tool list.
    assert listed["ttlMs"] == 3_600_000
    assert listed["cacheScope"] == "public"

    # A call that fails our own cross-field guard: no network, but the whole
    # call path runs and the message must reach the client (ToolError, not a
    # crash hidden behind "Error executing tool").
    called = server.request(
        3,
        "tools/call",
        {
            "name": "fdsnws_station_query_stations",
            "arguments": {"latitude": 41.9, "minlatitude": 40.0},
            **MODERN_META,
        },
    )["result"]
    assert called["isError"] is True
    assert "latitude and longitude must be given together" in called["content"][0]["text"]

    # A per-field constraint enforced by the SDK from the signature.
    called = server.request(
        4,
        "tools/call",
        {
            "name": "fdsnws_station_get_response",
            "arguments": {"network": "IV", "station": "A*", "channel": "HHZ"},
            **MODERN_META,
        },
    )["result"]
    assert called["isError"] is True
    assert "station" in called["content"][0]["text"]
    assert "pattern" in called["content"][0]["text"]

    # An unknown Datacenter name is answered with the list of available ones.
    called = server.request(
        5,
        "tools/call",
        {
            "name": "fdsnws_station_query_networks",
            "arguments": {"datacenter": "NOPE"},
            **MODERN_META,
        },
    )["result"]
    assert called["isError"] is True
    text = called["content"][0]["text"]
    assert "Unknown datacenter 'NOPE'" in text and "INGV" in text and "GFZ" in text


def test_stateless_request_without_envelope_is_rejected(server):
    reply = server.request(1, "tools/list", {})
    assert reply["error"]["code"] == -32602


def test_stderr_carries_no_protocol_noise(server):
    server.request(1, "tools/list", {**MODERN_META})
    err = server.close()
    assert "Traceback" not in err
