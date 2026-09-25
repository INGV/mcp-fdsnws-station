"""Live tests against the advertised Datacenters (opt-in: `pytest -m integration`).

Two layers over the same four Datacenters, so the multi-Datacenter claim is
exercised rather than asserted:

- the client functions (`query_text`, `get_response`): what each Datacenter
  answers and how it is parsed. The offline mirror is
  `tests/unit/test_parse_text.py`, which runs the parser on the captured
  responses of the same queries; keep the two in step.
- the four tools through `MCPServer.call_tool`, the dispatch a `tools/call`
  goes through on the wire: paging, the result models, the in-band error, the
  response-size limit and `inventory_to_dict` on live data. Assertions are on
  the structured result a client receives, validated against the tool's
  result model.

Both layers end with a configured alias of INGV: resolved through the
`FDSN_DATACENTERS` registry it must behave exactly like the ObsPy name, which
proves the resolution path an intranet deployment relies on.
"""

import asyncio
import re

import pytest
import requests

from fdsnws_station_server import client, server
from fdsnws_station_server.client import DatacenterRequestError, get_response, query_text
from fdsnws_station_server.models import (
    LIMIT_DEFAULT,
    RESPONSE_MAX_BYTES,
    ChannelEpoch,
    ChannelQueryResult,
    NetworkQueryResult,
    ResponseResult,
    StationEpoch,
    StationQueryResult,
)

pytestmark = pytest.mark.integration

# Per Datacenter: a network, a station and a channel pattern known to exist.
TARGETS = {
    "INGV": ("IV", "ACER", "HH?", "HHZ"),
    "EARTHSCOPE": ("IU", "ANMO", "BH?", "BHZ"),
    "GFZ": ("GE", "APE", "BH?", "BHZ"),
    "ORFEUS": ("NL", "HGN", "BH?", "BHZ"),
}
DATACENTERS = list(TARGETS)

# The window and Location codes of the captured response fixtures, which select
# exactly one Channel Epoch of each target. Without them the tool may omit the
# tree for size: IV.ACER..HHZ has three Epochs (66 kB of text block) and
# IU.ANMO.00.BHZ nine (110 kB), both over RESPONSE_MAX_BYTES; `*` at ANMO would
# add Location 10.
RESPONSE_WINDOW = {"starttime": "2024-01-01", "endtime": "2024-01-02"}
RESPONSE_LOCATION = {"INGV": "*", "EARTHSCOPE": "00", "GFZ": "*", "ORFEUS": "02"}

# A centre with stations within 30 km at each Datacenter.
RADIUS_CENTRES = {
    "INGV": (41.9, 12.5),
    "GFZ": (37.07, 25.52),
    "ORFEUS": (50.76, 5.93),
    "EARTHSCOPE": (34.95, -106.46),
}

RESULT_MODELS = {
    "fdsnws_station_query_networks": NetworkQueryResult,
    "fdsnws_station_query_stations": StationQueryResult,
    "fdsnws_station_query_channels": ChannelQueryResult,
    "fdsnws_station_get_response": ResponseResult,
}


def run(coro):
    return asyncio.run(coro)


# --- Client layer --------------------------------------------------------------


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_network_level(datacenter):
    net, _, _, _ = TARGETS[datacenter]
    epochs, api_url, _ = run(query_text("network", {"network": net}, datacenter))
    assert api_url.startswith(client.resolve_datacenter(datacenter)[1])
    assert epochs and epochs[0].network == net
    assert epochs[0].total_stations > 0


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_station_level(datacenter):
    net, sta, _, _ = TARGETS[datacenter]
    epochs, _, _ = run(query_text("station", {"network": net, "station": sta}, datacenter))
    assert epochs and all(e.station == sta for e in epochs)
    assert all(e.latitude is not None and e.site_name for e in epochs)


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_channel_level_is_sorted(datacenter):
    net, sta, cha, _ = TARGETS[datacenter]
    epochs, _, sent = run(
        query_text("channel", {"network": net, "station": sta, "channel": cha}, datacenter)
    )
    assert epochs and sent == {"network": net, "station": sta, "channel": cha}
    keys = [(e.network, e.station, e.location, e.channel, e.start_time) for e in epochs]
    assert keys == sorted(keys)
    assert all(e.scale is not None and e.sample_rate_hz for e in epochs)


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_get_response(datacenter):
    net, sta, _, cha = TARGETS[datacenter]
    inv, api_url = run(get_response(net, sta, "*", cha, None, None, datacenter))
    channels = [ch for n in inv for s in n for ch in s.channels]
    assert channels, f"{datacenter} returned no channel for {net}.{sta}.*.{cha}"
    assert all(ch.response is not None for ch in channels)
    assert "level=response" in api_url


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_no_data_is_empty_not_error(datacenter):
    epochs, _, _ = run(query_text("station", {"network": "ZZ", "station": "ZZZZ"}, datacenter))
    assert epochs == []


def test_ingv_whole_network_at_channel_level_is_thousands_of_epochs():
    """The reason for client-side paging: the full result is downloaded and paged here."""
    epochs, _, _ = run(query_text("channel", {"network": "IV"}, "INGV"))
    assert len(epochs) > 5000


def test_ingv_bad_window_returns_verbatim_400_body():
    with pytest.raises(DatacenterRequestError) as ei:
        run(
            query_text(
                "station",
                {"network": "IV", "starttime": "2030-01-01", "endtime": "2020-01-01"},
                "INGV",
            )
        )
    assert ei.value.status == 400
    assert ei.value.message.startswith("Error 400")


def test_radius_in_kilometres_is_accepted_everywhere():
    """maxradiuskm would be rejected by GFZ, ORFEUS and EarthScope (an INGV
    extension); the conversion to maxradius makes the request portable."""
    for datacenter, (lat, lon) in RADIUS_CENTRES.items():
        epochs, api_url, _ = run(
            query_text(
                "station", {"latitude": lat, "longitude": lon, "maxradiuskm": 30.0}, datacenter
            )
        )
        assert "maxradius=0.269808" in api_url and "maxradiuskm" not in api_url
        assert epochs, f"{datacenter} returned no station within 30 km of ({lat}, {lon})"


def test_configured_alias_of_ingv_resolves_through_the_registry(monkeypatch):
    monkeypatch.setattr(client, "CONFIGURED_DATACENTERS", {"MYINGV": "https://webservices.ingv.it"})
    epochs, api_url, _ = run(query_text("network", {"network": "IV"}, "myingv"))
    assert api_url.startswith("https://webservices.ingv.it/fdsnws/station/1/query?")
    assert epochs[0].network == "IV"
    inv, _ = run(get_response("IV", "ACER", "*", "HHZ", "2022-01-01", None, "MYINGV"))
    assert len(inv) == 1


# --- Tool layer ----------------------------------------------------------------


def call(tool: str, **arguments) -> dict:
    """One tool call as the SDK dispatches a `tools/call`; returns the
    `structuredContent` a client receives, after checking it against the tool's
    result model so a field dropped or renamed on the way out fails here."""
    wire = run(server.mcp.call_tool(tool, arguments))
    assert not wire.is_error, wire.content
    RESULT_MODELS[tool].model_validate(wire.structured_content)
    return wire.structured_content


def sort_keys(epochs: list[dict], model) -> list[tuple]:
    # Same key as client.sort_epochs: a blank Location code sorts first.
    return [tuple(e[f] or "" for f in model.SORT_KEY) for e in epochs]


def assert_first_page(result: dict, datacenter: str) -> None:
    assert result["datacenter"] == datacenter
    assert result["api_url"].startswith(client.resolve_datacenter(datacenter)[1])
    assert result["error"] is None and result["message"] is None
    assert result["pagination"]["total_count"] > 0
    assert result["pagination"]["returned_count"] == len(result["epochs"]) > 0


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_query_networks(datacenter):
    net, _, _, _ = TARGETS[datacenter]
    result = call("fdsnws_station_query_networks", network=net, datacenter=datacenter)
    assert_first_page(result, datacenter)
    assert all(e["network"] == net for e in result["epochs"])
    assert result["epochs"][0]["total_stations"] > 0


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_query_stations(datacenter):
    net, sta, _, _ = TARGETS[datacenter]
    result = call("fdsnws_station_query_stations", network=net, station=sta, datacenter=datacenter)
    assert_first_page(result, datacenter)
    epochs = result["epochs"]
    assert all(e["station"] == sta and e["site_name"] for e in epochs)
    assert all(e["latitude"] is not None and e["elevation_m"] is not None for e in epochs)


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_query_channels_is_typed_and_sorted(datacenter):
    net, sta, cha, _ = TARGETS[datacenter]
    result = call(
        "fdsnws_station_query_channels",
        network=net,
        station=sta,
        channel=cha,
        datacenter=datacenter,
    )
    assert_first_page(result, datacenter)
    epochs = result["epochs"]
    # The unit is in the field name because the text header has none.
    units = {"elevation_m", "depth_m", "azimuth_deg", "dip_deg", "scale_frequency_hz"}
    assert all(units <= e.keys() for e in epochs)
    assert all(e["scale"] is not None and e["sample_rate_hz"] for e in epochs)
    keys = sort_keys(epochs, ChannelEpoch)
    assert keys == sorted(keys)


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_get_response_returns_the_tree(datacenter):
    net, sta, _, cha = TARGETS[datacenter]
    result = call(
        "fdsnws_station_get_response",
        network=net,
        station=sta,
        location=RESPONSE_LOCATION[datacenter],
        channel=cha,
        datacenter=datacenter,
        **RESPONSE_WINDOW,
    )
    assert result["found"] is True and result["error"] is None and result["message"] is None
    assert result["channel_epochs_count"] == 1
    channels = [
        c for n in result["inventory"]["networks"] for s in n["stations"] for c in s["channels"]
    ]
    assert len(channels) == 1 and channels[0]["code"] == cha
    response = channels[0]["response"]
    assert response["instrument_sensitivity"]["value"] > 0 and response["response_stages"]


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_second_page_follows_the_first(datacenter):
    """Paging is ours, over the whole downloaded result, so two calls must cut
    one sorted sequence: same total, no Epoch twice, next_offset where page two
    starts. Station level of the whole network is over 100 Epochs everywhere."""
    net, _, _, _ = TARGETS[datacenter]
    limit = 5
    first = call("fdsnws_station_query_stations", network=net, limit=limit, datacenter=datacenter)
    offset = first["pagination"]["next_offset"]
    second = call(
        "fdsnws_station_query_stations",
        network=net,
        limit=limit,
        offset=offset,
        datacenter=datacenter,
    )
    p1, p2 = first["pagination"], second["pagination"]
    assert p1["total_count"] == p2["total_count"] > 2 * limit
    assert p1["has_more"] is True and offset == limit == p2["offset"]
    assert p2["returned_count"] == limit and p2["has_more"] is True
    assert p2["next_offset"] == 2 * limit
    records = [tuple(e.values()) for e in first["epochs"]]
    assert set(records).isdisjoint(tuple(e.values()) for e in second["epochs"])
    keys = sort_keys(first["epochs"] + second["epochs"], StationEpoch)
    assert keys == sorted(keys)


def test_tool_ingv_whole_network_at_channel_level_is_one_page_of_an_exact_total():
    """Thousands of Epochs downloaded, one page returned, and the total is the
    exact count of the full result, not an estimate."""
    result = call("fdsnws_station_query_channels", network="IV", datacenter="INGV")
    page = result["pagination"]
    assert page["total_count"] > 5000
    assert page["returned_count"] == len(result["epochs"]) == LIMIT_DEFAULT
    assert page["has_more"] is True and page["next_offset"] == LIMIT_DEFAULT


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_no_data_is_a_message_not_an_error(datacenter):
    # Station level: ZZ alone is a temporary-network code that EarthScope and
    # GFZ do serve at network level.
    result = call(
        "fdsnws_station_query_stations", network="ZZ", station="ZZZZ", datacenter=datacenter
    )
    assert result["error"] is None and result["epochs"] == []
    assert result["pagination"]["total_count"] == 0
    assert result["message"].startswith(f"No station epochs matched at {datacenter}")


def test_tool_ingv_bad_window_is_an_in_band_verbatim_400():
    result = call(
        "fdsnws_station_query_stations",
        network="IV",
        starttime="2030-01-01",
        endtime="2020-01-01",
        datacenter="INGV",
    )
    assert result["pagination"] is None and result["epochs"] == []
    assert result["error"]["status"] == 400
    # Verbatim: the same request sent directly returns the same body, up to
    # INGV's per-request timestamp line.
    direct = requests.get(result["api_url"], timeout=60)
    assert direct.status_code == 400
    stamp = "Request Submitted:"
    assert result["error"]["message"].split(stamp)[0] == direct.text.strip().split(stamp)[0]


def test_tool_response_over_the_limit_lists_every_epoch_window():
    result = call(
        "fdsnws_station_get_response",
        network="IV",
        station="ACER",
        channel="HHZ",
        datacenter="INGV",
    )
    count = result["channel_epochs_count"]
    assert result["found"] is True and result["inventory"] is None and count > 1
    message = result["message"]
    assert int(re.search(r"is (\d+) bytes", message).group(1)) > RESPONSE_MAX_BYTES
    assert message.count("IV.ACER..HHZ ") == count


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_tool_radius_in_kilometres_is_sent_in_degrees(datacenter):
    lat, lon = RADIUS_CENTRES[datacenter]
    result = call(
        "fdsnws_station_query_stations",
        latitude=lat,
        longitude=lon,
        maxradiuskm=30.0,
        datacenter=datacenter,
    )
    assert "maxradius=0.269808" in result["api_url"] and "maxradiuskm" not in result["api_url"]
    assert "maxradiuskm" not in result["query"]
    assert result["error"] is None and result["pagination"]["total_count"] > 0


def test_tool_configured_alias_of_ingv_resolves_through_the_registry(monkeypatch):
    monkeypatch.setattr(client, "CONFIGURED_DATACENTERS", {"MYINGV": "https://webservices.ingv.it"})
    result = call("fdsnws_station_query_networks", network="IV", datacenter="myingv")
    assert result["datacenter"] == "MYINGV" and result["error"] is None
    assert result["api_url"].startswith("https://webservices.ingv.it/fdsnws/station/1/query?")
    assert result["epochs"][0]["network"] == "IV"
    response = call(
        "fdsnws_station_get_response",
        network="IV",
        station="ACER",
        channel="HHZ",
        datacenter="MYINGV",
        **RESPONSE_WINDOW,
    )
    assert response["datacenter"] == "MYINGV" and response["found"] is True
    assert response["api_url"].startswith("https://webservices.ingv.it/")
    assert response["inventory"] is not None
