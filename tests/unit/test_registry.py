"""Datacenter registry: FDSN_DATACENTERS parsing, shadowing, defaults."""

import pytest

from fdsnws_station_server import client
from fdsnws_station_server.client import parse_datacenters, resolve_datacenter


def test_parse_two_entries_upper_cases_names_and_strips_trailing_slash():
    assert parse_datacenters(
        "RSNI=http://stationxml.intranet:8080/, lab=https://lab.example.org"
    ) == {
        "RSNI": "http://stationxml.intranet:8080",
        "LAB": "https://lab.example.org",
    }


def test_parse_empty_and_blank_entries():
    assert parse_datacenters("") == {}
    assert parse_datacenters(" , ,") == {}


@pytest.mark.parametrize("raw", ["RSNI", "RSNI=ftp://x", "=http://x", "RSNI=", "http://x"])
def test_parse_rejects_malformed_entries(raw):
    with pytest.raises(ValueError, match="FDSN_DATACENTERS"):
        parse_datacenters(raw)


def test_obspy_names_resolve_case_insensitively():
    assert resolve_datacenter("INGV") == ("INGV", "https://webservices.ingv.it")
    assert resolve_datacenter("gfz") == ("GFZ", "https://geofon.gfz.de")
    assert resolve_datacenter("EarthScope") == ("EARTHSCOPE", "https://service.earthscope.org")
    assert resolve_datacenter("orfeus") == ("ORFEUS", "https://www.orfeus-eu.org")


def test_none_resolves_to_default(monkeypatch):
    monkeypatch.setattr(client, "DEFAULT_DATACENTER", "GFZ")
    assert resolve_datacenter(None)[0] == "GFZ"


def test_configured_name_is_resolved_and_shadows_obspy(monkeypatch):
    monkeypatch.setattr(
        client,
        "CONFIGURED_DATACENTERS",
        {"RSNI": "http://stationxml.intranet:8080", "INGV": "http://mirror.intranet"},
    )
    assert resolve_datacenter("rsni") == ("RSNI", "http://stationxml.intranet:8080")
    assert resolve_datacenter("INGV") == ("INGV", "http://mirror.intranet")


def test_unknown_name_lists_configured_and_obspy_names(monkeypatch):
    monkeypatch.setattr(client, "CONFIGURED_DATACENTERS", {"RSNI": "http://x"})
    with pytest.raises(ValueError) as ei:
        resolve_datacenter("NOPE")
    msg = str(ei.value)
    assert "Unknown datacenter 'NOPE'" in msg
    assert msg.index("RSNI") < msg.index("INGV")  # configured first
    for name in ("EARTHSCOPE", "GFZ", "ORFEUS"):
        assert name in msg
