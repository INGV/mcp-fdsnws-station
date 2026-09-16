"""Live tests against the advertised Datacenters (opt-in: `pytest -m integration`).

One query per Level and one `get_response` per Datacenter, so the
multi-Datacenter claim is exercised rather than asserted. The offline mirror is
`tests/unit/test_parse_text.py`, which runs the parser on the captured
responses of the same queries; keep the two in step.

The last test drives the whole tool through the `FDSN_DATACENTERS` registry:
an alias of INGV configured in the environment must behave exactly like the
ObsPy name, which proves the resolution path an intranet deployment relies on.
"""

import asyncio

import pytest

from fdsnws_station_server import client
from fdsnws_station_server.client import DatacenterRequestError, get_response, query_text

pytestmark = pytest.mark.integration

# Per Datacenter: a network, a station and a channel pattern known to exist.
TARGETS = {
    "INGV": ("IV", "ACER", "HH?", "HHZ"),
    "EARTHSCOPE": ("IU", "ANMO", "BH?", "BHZ"),
    "GFZ": ("GE", "APE", "BH?", "BHZ"),
    "ORFEUS": ("NL", "HGN", "BH?", "BHZ"),
}
DATACENTERS = list(TARGETS)


def run(coro):
    return asyncio.run(coro)


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
    """The reason for ADR-0001: the full result is downloaded and paged here."""
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
    for datacenter, (lat, lon) in {
        "INGV": (41.9, 12.5),
        "GFZ": (37.07, 25.52),
        "ORFEUS": (50.76, 5.93),
        "EARTHSCOPE": (34.95, -106.46),
    }.items():
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
