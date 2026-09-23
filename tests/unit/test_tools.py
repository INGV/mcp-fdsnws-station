"""The tool bodies end to end, with the network mocked: how a Datacenter's answer is
folded into the one result model each tool returns."""

import asyncio
from unittest.mock import patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fdsnws_station_server import client, server
from fdsnws_station_server.client import sort_epochs
from fdsnws_station_server.models import NetworkEpoch


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def run(coro):
    return asyncio.run(coro)


def test_query_tool_pages_sorted_epochs_and_echoes_the_request(fixture_text):
    raw = fixture_text("ingv_station_iv_a.txt")
    with patch.object(client.requests, "get", return_value=FakeResponse(200, raw)):
        result = run(server.fdsnws_station_query_stations(network="IV", station="A*", limit=10))
    assert result.datacenter == "INGV" and result.level == "station"
    assert result.error is None and result.message is None
    assert result.query == {"network": "IV", "station": "A*"}
    assert result.api_url.endswith("?format=text&level=station&network=IV&station=A%2A")
    assert result.pagination.model_dump() == {
        "total_count": 36,
        "returned_count": 10,
        "limit": 10,
        "offset": 0,
        "has_more": True,
        "next_offset": 10,
    }
    assert [e.station for e in result.epochs][:3] == ["ACATE", "ACER", "AGLI"]


def test_query_tool_second_page(fixture_text):
    raw = fixture_text("ingv_station_iv_a.txt")
    with patch.object(client.requests, "get", return_value=FakeResponse(200, raw)):
        first = run(server.fdsnws_station_query_stations(network="IV", limit=30))
        second = run(server.fdsnws_station_query_stations(network="IV", limit=30, offset=30))
    assert second.pagination.returned_count == 6 and second.pagination.has_more is False
    assert {e.station for e in first.epochs}.isdisjoint({e.station for e in second.epochs})


def test_query_tool_empty_result_has_message_not_error():
    with patch.object(client.requests, "get", return_value=FakeResponse(204)):
        result = run(server.fdsnws_station_query_networks(network="ZZ"))
    assert result.epochs == [] and result.error is None
    assert result.pagination.total_count == 0 and result.pagination.has_more is False
    assert result.message.startswith("No network epochs matched at INGV")


def test_query_tool_upstream_400_is_in_band(fixture_text):
    body = fixture_text("ingv_bad_window.error.txt")
    with patch.object(client.requests, "get", return_value=FakeResponse(400, body)):
        result = run(
            server.fdsnws_station_query_channels(
                network="IV", starttime="2030-01-01", endtime="2020-01-01"
            )
        )
    assert result.error.status == 400 and result.error.message == body.strip()
    assert result.pagination is None and result.epochs == [] and result.message is None
    assert result.query == {"network": "IV", "starttime": "2030-01-01", "endtime": "2020-01-01"}
    assert "starttime=2030-01-01" in result.api_url


def test_query_tool_unparseable_200_is_in_band_not_a_crash():
    html = "<html><body>Welcome to the intranet portal</body></html>"
    with patch.object(client.requests, "get", return_value=FakeResponse(200, html)):
        result = run(server.fdsnws_station_query_stations(network="IV"))
    assert result.error.status == 200
    assert result.error.message.startswith("Unparseable response")
    assert "no # header" in result.error.message
    assert result.pagination is None


def test_query_tool_line_missing_a_code_column_is_in_band():
    raw = "#Description|StartTime|EndTime|TotalStations\nItaly|1988-01-01T00:00:00||561\n"
    with patch.object(client.requests, "get", return_value=FakeResponse(200, raw)):
        result = run(server.fdsnws_station_query_networks())
    assert result.error.status == 200 and "level=network" in result.error.message


def test_query_tool_radial_search_sends_degrees():
    with patch.object(client.requests, "get", return_value=FakeResponse(204)) as get:
        result = run(
            server.fdsnws_station_query_stations(latitude=41.9, longitude=12.5, maxradiuskm=50.0)
        )
    assert result.query == {"latitude": 41.9, "longitude": 12.5, "maxradius": 0.449681}
    assert "maxradius=0.449681" in get.call_args.args[0]


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ({"latitude": 41.9}, "together"),
        ({"maxradiuskm": 50.0}, "need latitude and longitude"),
        ({"minlatitude": 40.0, "maxradiuskm": 50.0}, "mutually exclusive"),
        ({"minlatitude": 40.0, "latitude": 41.9, "longitude": 12.5}, "mutually exclusive"),
    ],
)
def test_geographic_guard_is_a_tool_error_with_its_message(arguments, expected):
    for tool in (server.fdsnws_station_query_stations, server.fdsnws_station_query_channels):
        with patch.object(client.requests, "get") as get:
            with pytest.raises(ToolError, match=expected):
                run(tool(**arguments))
        get.assert_not_called()


def test_unknown_datacenter_is_a_tool_error_listing_names():
    with pytest.raises(ToolError, match="Unknown datacenter 'NOPE'. Available: .*INGV"):
        run(server.fdsnws_station_query_networks(datacenter="NOPE"))


def test_network_level_sorts_by_code_then_start():
    epochs = [
        NetworkEpoch(network="IV", start_time="2000-01-01T00:00:00"),
        NetworkEpoch(network="GE", start_time="1993-01-01T00:00:00"),
        NetworkEpoch(network="IV", start_time="1988-01-01T00:00:00"),
    ]
    assert [(e.network, e.start_time) for e in sort_epochs(epochs, "network")] == [
        ("GE", "1993-01-01T00:00:00"),
        ("IV", "1988-01-01T00:00:00"),
        ("IV", "2000-01-01T00:00:00"),
    ]


def test_get_response_folds_upstream_failure_and_not_found():
    from obspy.clients.fdsn.header import FDSNBadRequestException, FDSNNoDataException

    class NoData:
        def __init__(self, base_url, timeout):
            pass

        def get_stations(self, **kwargs):
            raise FDSNNoDataException("No data available for request.")

    class Bad(NoData):
        def get_stations(self, **kwargs):
            raise FDSNBadRequestException("Bad request.\nHTTP Status code: 400\nSyntax Error")

    with patch.object(client, "Client", NoData):
        result = run(server.fdsnws_station_get_response("IV", "ZZZZ", "HHZ"))
    assert result.found is False and result.inventory is None and result.error is None
    assert result.channel_epochs_count == 0 and result.location == "*"
    assert result.message.startswith("No response found at INGV for IV.ZZZZ.*.HHZ")

    with patch.object(client, "Client", Bad):
        result = run(server.fdsnws_station_get_response("IV", "ACER", "HHZ", location="--"))
    assert result.found is False and "Syntax Error" in result.error.message
    assert result.error.status == 400 and "location=--" in result.api_url


def test_published_limit_maximum_is_the_calibrated_cap():
    # The maximum is what the model is told it may ask for, so it is read back
    # from the schema a client receives, not from the constant.
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    for name in (
        "fdsnws_station_query_networks",
        "fdsnws_station_query_stations",
        "fdsnws_station_query_channels",
    ):
        limit = tools[name].input_schema["properties"]["limit"]
        assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 70, 50)
        assert "max 70" in tools[name].description
