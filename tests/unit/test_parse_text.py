"""Header-driven parsing of `format=text` captured from the four advertised Datacenters.

The fixtures are real responses, not hand-written: the header quirks the parser
must absorb (INGV spaces and lower-case `location`, GFZ and ORFEUS compact,
EarthScope trailing space and four-decimal times) only exist in live output.
"""

import pytest

from fdsnws_station_server.client import parse_text, sort_epochs
from fdsnws_station_server.models import ChannelEpoch, NetworkEpoch, StationEpoch

DATACENTERS = ["ingv", "gfz", "orfeus", "earthscope"]


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_network_level_parses_every_datacenter(datacenter, fixture_text):
    epochs = parse_text(fixture_text(f"{datacenter}_network.txt"), "network")
    assert len(epochs) == 1
    ep = epochs[0]
    assert isinstance(ep, NetworkEpoch)
    assert ep.network in ("IV", "GE", "NL", "IU")
    assert ep.description
    assert ep.start_time
    assert ep.end_time is None  # every one of these networks is still open
    assert isinstance(ep.total_stations, int) and ep.total_stations > 0


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_station_level_parses_every_datacenter(datacenter, fixture_text):
    epochs = parse_text(fixture_text(f"{datacenter}_station.txt"), "station")
    assert epochs
    for ep in epochs:
        assert isinstance(ep, StationEpoch)
        assert -90 <= ep.latitude <= 90
        assert -180 <= ep.longitude <= 180
        assert isinstance(ep.elevation_m, float)
        assert ep.site_name
        assert ep.start_time


@pytest.mark.parametrize("datacenter", DATACENTERS)
def test_channel_level_parses_every_datacenter(datacenter, fixture_text):
    epochs = parse_text(fixture_text(f"{datacenter}_channel.txt"), "channel")
    assert epochs
    for ep in epochs:
        assert isinstance(ep, ChannelEpoch)
        assert isinstance(ep.location, str)  # blank code is "", never None
        assert len(ep.channel) == 3
        assert isinstance(ep.scale, float)
        # ScaleFreq is what every Datacenter emits; it must land on the spec-named field.
        assert isinstance(ep.scale_frequency_hz, float)
        assert ep.scale_units
        assert isinstance(ep.sample_rate_hz, float)
        assert isinstance(ep.azimuth_deg, float)
        assert isinstance(ep.dip_deg, float)


def test_ingv_header_with_spaces_and_lowercase_location(fixture_text):
    raw = fixture_text("ingv_channel.txt")
    assert raw.startswith("#Network | Station | location | Channel")
    ep = parse_text(raw, "channel")[0]
    assert (ep.network, ep.station, ep.channel) == ("IV", "ACER", "HHE")
    assert ep.sensor_description == "NANOMETRICS TRILLIUM-40S"
    assert ep.scale == 1500000000.0 and ep.scale_frequency_hz == 0.2 and ep.scale_units == "m/s"
    assert ep.elevation_m == 690.0 and ep.depth_m == 1.0
    assert ep.azimuth_deg == 90.0 and ep.dip_deg == 0.0
    assert ep.sample_rate_hz == 100.0


def test_gfz_compact_header(fixture_text):
    raw = fixture_text("gfz_channel.txt")
    assert raw.startswith("#Network|Station|Location|Channel|")
    ep = parse_text(raw, "channel")[0]
    assert (ep.network, ep.station, ep.channel) == ("GE", "APE", "BHE")
    assert ep.scale_units == "M/S"  # upper-case units pass through verbatim


def test_non_blank_location_codes_are_kept(fixture_text):
    epochs = parse_text(fixture_text("earthscope_channel.txt"), "channel")
    assert {e.location for e in epochs} == {"", "00", "10"}


def test_earthscope_trailing_space_and_four_decimal_times(fixture_text):
    raw = fixture_text("earthscope_station.txt")
    header = raw.splitlines()[0]
    assert header.endswith("EndTime ")
    epochs = parse_text(raw, "station")
    assert epochs[0].start_time == "1989-08-29T00:00:00.0000"
    assert epochs[0].end_time == "2000-10-19T16:00:00.0000"
    assert epochs[0].site_name == "Albuquerque, New Mexico, USA"


def test_earthscope_scientific_notation_scale(fixture_text):
    ep = parse_text(fixture_text("earthscope_channel.txt"), "channel")[0]
    assert ep.scale == 8.48699e8


def test_specification_scalefrequency_spelling_maps_to_same_field():
    raw = (
        "#Network|Station|Location|Channel|Latitude|Longitude|Elevation|Depth|Azimuth|Dip|"
        "SensorDescription|Scale|ScaleFrequency|ScaleUnits|SampleRate|StartTime|EndTime\n"
        "XX|STA|00|HHZ|1.0|2.0|3.0|0.0|0.0|-90.0|Sensor|1000.0|1.0|m/s|100.0|2020-01-01T00:00:00|\n"
    )
    ep = parse_text(raw, "channel")[0]
    assert ep.scale_frequency_hz == 1.0
    assert ep.location == "00"
    assert ep.end_time is None


def test_unknown_column_ignored_and_missing_column_is_none():
    raw = (
        "#Network|Station|Latitude|Longitude|Elevation|SiteName|StartTime|EndTime|Extra\n"
        "IV|ACER|40.7867|15.9427||Acerenza|2007-07-05T12:00:00||whatever\n"
    )
    ep = parse_text(raw, "station")[0]
    assert ep.elevation_m is None
    assert not hasattr(ep, "extra")
    raw = "#Network|Station|StartTime\nIV|ACER|2007-07-05T12:00:00\n"
    ep = parse_text(raw, "station")[0]
    assert ep.latitude is None and ep.site_name is None and ep.end_time is None


def test_blank_lines_and_extra_comment_lines_are_skipped():
    raw = (
        "\n#Network|Description|StartTime|EndTime|TotalStations\n\n"
        "# another comment\nIV|Italy|1988-01-01T00:00:00||561\n\n"
    )
    epochs = parse_text(raw, "network")
    assert len(epochs) == 1 and epochs[0].total_stations == 561


def test_empty_body_yields_no_epochs():
    assert parse_text("", "channel") == []


def test_sort_is_by_level_key_not_datacenter_order(fixture_text):
    epochs = parse_text(fixture_text("ingv_station_iv_a.txt"), "station")
    shuffled = epochs[::-1]
    ordered = sort_epochs(shuffled, "station")
    keys = [(e.network, e.station, e.start_time) for e in ordered]
    assert keys == sorted(keys)
    assert ordered[0].station == "ACATE"


def test_sort_channel_key_includes_location_and_channel(fixture_text):
    epochs = parse_text(fixture_text("earthscope_channel.txt"), "channel")
    ordered = sort_epochs(epochs[::-1], "channel")
    keys = [(e.network, e.station, e.location, e.channel, e.start_time) for e in ordered]
    assert keys == sorted(keys)
    # EarthScope's four-decimal times sort correctly as strings because the
    # Datacenter is internally consistent; this pins that assumption.
    same_channel = [e.start_time for e in ordered if e.channel == "BHZ" and e.location == "00"]
    assert same_channel == sorted(same_channel)
