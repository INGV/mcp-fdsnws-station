"""Response path: Inventory serialisation from captured StationXML, and error mapping
with ObsPy's Client mocked."""

import asyncio
import json
from unittest.mock import patch

import pytest
from obspy import Inventory, read_inventory
from obspy.clients.fdsn.header import FDSNBadRequestException, FDSNNoDataException

from fdsnws_station_server import client
from fdsnws_station_server.client import DatacenterRequestError, get_response, inventory_to_dict

FIXTURE = "tests/fixtures/ingv_iv_acer_hhz_response.xml"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def tree():
    return inventory_to_dict(read_inventory(FIXTURE))


def test_tree_is_json_serialisable_and_rooted_at_networks(tree):
    json.dumps(tree)
    assert list(tree)[0] == "networks"
    net = tree["networks"][0]
    assert net["code"] == "IV"
    assert net["description"] == "Italian Seismic Network"
    sta = net["stations"][0]
    assert sta["code"] == "ACER"
    assert sta["site"]["name"] == "Acerenza"
    assert sta["latitude"] == pytest.approx(40.7867)


def test_channels_keep_stationxml_values_and_units(tree):
    channels = tree["networks"][0]["stations"][0]["channels"]
    assert len(channels) == 3  # three HHZ Epochs at ACER
    ch = channels[0]
    assert ch["code"] == "HHZ" and ch["location_code"] == ""
    assert ch["depth"] == 1.0 and ch["dip"] == -90.0 and ch["sample_rate"] == 100.0
    assert ch["start_date"] == "2007-07-05T12:00:00.000000Z"
    assert ch["sensor"]["description"] == "NANOMETRICS TRILLIUM-40S"


def test_response_carries_sensitivity_and_stages(tree):
    resp = tree["networks"][0]["stations"][0]["channels"][0]["response"]
    sens = resp["instrument_sensitivity"]
    assert sens == {
        "value": 1500000000.0,
        "frequency": 0.2,
        "input_units": "m/s",
        "output_units": "count",
    }
    stages = resp["response_stages"]
    assert len(stages) == 5
    pz = stages[0]
    assert pz["stage_sequence_number"] == 1
    assert pz["pz_transfer_function_type"] == "LAPLACE (RADIANS/SECOND)"
    assert pz["normalization_frequency"] == 0.2
    assert pz["stage_gain"] == 1500.0
    # Poles and zeros are complex in ObsPy; JSON gets real/imag pairs.
    assert pz["zeros"] == [{"real": 0.0, "imag": 0.0}, {"real": 0.0, "imag": 0.0}]
    assert pz["poles"][0] == {"real": -0.1111, "imag": 0.1111}


def test_private_attributes_and_none_values_are_dropped(tree):
    text = json.dumps(tree)
    assert '"_' not in text
    assert "null" not in text


def test_get_response_returns_inventory_and_api_url():
    inv = read_inventory(FIXTURE)

    class FakeClient:
        def __init__(self, base_url, timeout):
            self.base_url = base_url

        def get_stations(self, **kwargs):
            assert kwargs["level"] == "response"
            assert kwargs["starttime"] == "2022-01-01"  # verbatim, ObsPy parses it
            return inv

    with patch.object(client, "Client", FakeClient):
        got, api_url = run(get_response("IV", "ACER", "*", "HHZ", "2022-01-01", None, "INGV"))
    assert got is inv
    assert api_url.startswith("https://webservices.ingv.it/fdsnws/station/1/query?")
    assert "level=response" in api_url and "channel=HHZ" in api_url
    assert "starttime=2022-01-01" in api_url


def test_get_response_no_data_is_empty_inventory():
    class FakeClient:
        def __init__(self, base_url, timeout):
            pass

        def get_stations(self, **kwargs):
            raise FDSNNoDataException("No data available for request.")

    with patch.object(client, "Client", FakeClient):
        inv, _ = run(get_response("IV", "ZZZZ", "*", "HHZ", None, None, "INGV"))
    assert isinstance(inv, Inventory) and len(inv) == 0


def test_get_response_bad_request_maps_to_datacenter_error():
    class FakeClient:
        def __init__(self, base_url, timeout):
            pass

        def get_stations(self, **kwargs):
            raise FDSNBadRequestException("Bad request.\nHTTP Status code: 400\nSyntax Error")

    with patch.object(client, "Client", FakeClient):
        with pytest.raises(DatacenterRequestError) as ei:
            run(get_response("IV", "ACER", "*", "HHZ", "2030-01-01", "2020-01-01", "INGV"))
    assert "Syntax Error" in ei.value.message
    assert ei.value.status == 400
    assert "starttime=2030-01-01&endtime=2020-01-01" in ei.value.api_url


def test_get_response_404_is_no_data_like_the_text_path():
    from obspy.clients.fdsn.header import FDSNException

    class FakeClient:
        def __init__(self, base_url, timeout):
            pass

        def get_stations(self, **kwargs):
            raise FDSNException("Unknown HTTP code: 404", "Not Found")

    with patch.object(client, "Client", FakeClient):
        inv, _ = run(get_response("IV", "ACER", "*", "HHZ", None, None, "INGV"))
    assert len(inv) == 0


def test_get_response_unknown_status_is_carried():
    from obspy.clients.fdsn.header import FDSNException

    class FakeClient:
        def __init__(self, base_url, timeout):
            pass

        def get_stations(self, **kwargs):
            raise FDSNException("Unknown HTTP code: 502", "Bad Gateway")

    with patch.object(client, "Client", FakeClient):
        with pytest.raises(DatacenterRequestError) as ei:
            run(get_response("IV", "ACER", "*", "HHZ", None, None, "INGV"))
    assert ei.value.status == 502 and "Bad Gateway" in ei.value.message
