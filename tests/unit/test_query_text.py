"""The text path with the network mocked: URL building, radii conversion, status mapping."""

import asyncio
from unittest.mock import patch

import pytest
import requests

from fdsnws_station_server import client
from fdsnws_station_server.client import (
    KM_PER_DEGREE,
    DatacenterRequestError,
    build_query_url,
    query_text,
    upstream_params,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def run(coro):
    return asyncio.run(coro)


def test_upstream_params_drops_none_and_default_includerestricted():
    assert upstream_params({"network": "IV", "station": None, "includerestricted": True}) == {
        "network": "IV"
    }
    assert upstream_params({"includerestricted": False}) == {"includerestricted": "false"}


def test_kilometre_radii_become_degrees():
    sent = upstream_params({"latitude": 41.9, "longitude": 12.5, "maxradiuskm": 50.0})
    assert "maxradiuskm" not in sent
    assert sent["maxradius"] == pytest.approx(50.0 / KM_PER_DEGREE, abs=1e-6)
    sent = upstream_params({"minradiuskm": 111.19, "maxradiuskm": 222.38})
    assert sent == {"minradius": 1.0, "maxradius": 2.0}


def test_build_query_url_has_format_level_and_encodes_wildcards():
    url = build_query_url(
        "https://webservices.ingv.it", "channel", {"network": "IV", "channel": "HH?"}
    )
    assert url == (
        "https://webservices.ingv.it/fdsnws/station/1/query"
        "?format=text&level=channel&network=IV&channel=HH%3F"
    )


def test_query_text_200_returns_sorted_epochs_url_and_sent(fixture_text):
    raw = fixture_text("ingv_station_iv_a.txt")
    with patch.object(client.requests, "get", return_value=FakeResponse(200, raw)) as get:
        epochs, api_url, sent = run(
            query_text("station", {"network": "IV", "station": "A*", "maxradiuskm": None}, "INGV")
        )
    assert len(epochs) == 36
    assert [e.station for e in epochs] == sorted(e.station for e in epochs)
    assert api_url.startswith("https://webservices.ingv.it/fdsnws/station/1/query?format=text")
    assert "level=station" in api_url and "station=A%2A" in api_url
    assert sent == {"network": "IV", "station": "A*"}
    get.assert_called_once()
    assert get.call_args.args[0] == api_url
    assert get.call_args.kwargs["timeout"] == 45.0


def test_query_text_radius_in_degrees_appears_in_url():
    with patch.object(client.requests, "get", return_value=FakeResponse(204)):
        _, api_url, sent = run(
            query_text(
                "station", {"latitude": 41.9, "longitude": 12.5, "maxradiuskm": 50.0}, "INGV"
            )
        )
    assert "maxradius=0.449681" in api_url and "maxradiuskm" not in api_url
    assert sent["maxradius"] == 0.449681


@pytest.mark.parametrize("status", [204, 404])
def test_no_data_statuses_yield_empty_list(status):
    with patch.object(client.requests, "get", return_value=FakeResponse(status)):
        epochs, api_url, _ = run(query_text("network", {"network": "ZZ"}, "INGV"))
    assert epochs == [] and "network=ZZ" in api_url


def test_400_raises_with_verbatim_body_and_request(fixture_text):
    body = fixture_text("ingv_bad_window.error.txt")
    with patch.object(client.requests, "get", return_value=FakeResponse(400, body)):
        with pytest.raises(DatacenterRequestError) as ei:
            run(
                query_text(
                    "station",
                    {"network": "IV", "starttime": "2030-01-01", "endtime": "2020-01-01"},
                    "INGV",
                )
            )
    err = ei.value
    assert err.status == 400
    assert err.message == body.strip()
    assert err.message.startswith("Error 400: Bad request")
    assert "starttime=2030-01-01" in err.api_url
    assert err.query == {"network": "IV", "starttime": "2030-01-01", "endtime": "2020-01-01"}


def test_500_with_empty_body_gets_a_status_message():
    with patch.object(client.requests, "get", return_value=FakeResponse(503, "  ")):
        with pytest.raises(DatacenterRequestError) as ei:
            run(query_text("network", {}, "INGV"))
    assert (ei.value.status, ei.value.message) == (503, "HTTP 503")


def test_network_failure_raises_without_status():
    with patch.object(client.requests, "get", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(DatacenterRequestError) as ei:
            run(query_text("network", {}, "INGV"))
    assert ei.value.status is None
    assert "refused" in ei.value.message
    assert ei.value.api_url.startswith("https://webservices.ingv.it")


def test_query_text_uses_configured_datacenter(monkeypatch):
    monkeypatch.setattr(
        client, "CONFIGURED_DATACENTERS", {"RSNI": "http://stationxml.intranet:8080"}
    )
    with patch.object(client.requests, "get", return_value=FakeResponse(204)):
        _, api_url, _ = run(query_text("network", {}, "rsni"))
    assert api_url.startswith("http://stationxml.intranet:8080/fdsnws/station/1/query?")


def test_timeout_env(monkeypatch):
    monkeypatch.setenv("FDSN_TIMEOUT", "7.5")
    with patch.object(client.requests, "get", return_value=FakeResponse(204)) as get:
        run(query_text("network", {}, "INGV"))
    assert get.call_args.kwargs["timeout"] == 7.5
    monkeypatch.setenv("FDSN_TIMEOUT", "soon")
    with patch.object(client.requests, "get", return_value=FakeResponse(204)) as get:
        run(query_text("network", {}, "INGV"))
    assert get.call_args.kwargs["timeout"] == 45.0
