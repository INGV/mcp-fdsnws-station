"""Input constraints, the geographic guard, and client-side pagination."""

import pytest
from pydantic import TypeAdapter, ValidationError

from fdsnws_station_server.models import (
    Code,
    ExactCode,
    IsoTime,
    Limit,
    Offset,
    Pagination,
    StationEpoch,
    check_geographic_selection,
    paginate,
)


@pytest.mark.parametrize("value", ["IV", "IV,GE", "I*", "H??", "--", "A-B", "a" * 64])
def test_code_accepts_specification_forms(value):
    assert TypeAdapter(Code).validate_python(value) == value


@pytest.mark.parametrize("value", ["", "IV GE", "IV;GE", "a" * 65, "IV|GE", "x/y"])
def test_code_rejects_unsafe_forms(value):
    with pytest.raises(ValidationError):
        TypeAdapter(Code).validate_python(value)


@pytest.mark.parametrize("value", ["IV", "ACER", "HHZ", "A-1"])
def test_exact_code_accepts_single_codes(value):
    assert TypeAdapter(ExactCode).validate_python(value) == value


@pytest.mark.parametrize("value", ["IV,GE", "A*", "HH?", "IV GE", ""])
def test_exact_code_rejects_lists_and_wildcards(value):
    with pytest.raises(ValidationError):
        TypeAdapter(ExactCode).validate_python(value)


@pytest.mark.parametrize(
    "value",
    ["2020-01-01", "2020-01-01T00:00:00", "2020-01-01T12:34:56.789", "2020-01-01T00:00:00Z"],
)
def test_iso_time_is_checked_structurally_and_kept_verbatim(value):
    assert TypeAdapter(IsoTime).validate_python(value) == value


@pytest.mark.parametrize("value", ["yesterday", "01/02/2020", "2020-13-01", ""])
def test_iso_time_rejects_non_iso(value):
    with pytest.raises(ValidationError, match="ISO 8601"):
        TypeAdapter(IsoTime).validate_python(value)


def test_limit_and_offset_bounds():
    assert TypeAdapter(Limit).validate_python(500) == 500
    for bad in (0, 501):
        with pytest.raises(ValidationError):
            TypeAdapter(Limit).validate_python(bad)
    assert TypeAdapter(Offset).validate_python(0) == 0
    with pytest.raises(ValidationError):
        TypeAdapter(Offset).validate_python(-1)


def test_geographic_guard_accepts_bbox_alone_and_radial_alone():
    check_geographic_selection(40.0, 43.0, 12.0, 15.0, None, None, None, None)
    check_geographic_selection(None, None, None, None, 41.9, 12.5, None, 50.0)
    check_geographic_selection(None, None, None, None, 41.9, 12.5, None, None)
    check_geographic_selection(None, None, None, None, None, None, None, None)


def test_geographic_guard_rejects_bbox_with_radial():
    with pytest.raises(ValueError, match="mutually exclusive"):
        check_geographic_selection(40.0, None, None, None, 41.9, 12.5, None, None)
    # A radius alone is radial too: the guard must not be bypassed by omitting the centre.
    with pytest.raises(ValueError, match="mutually exclusive"):
        check_geographic_selection(40.0, None, None, None, None, None, None, 50.0)


def test_geographic_guard_requires_latitude_and_longitude_together():
    with pytest.raises(ValueError, match="together"):
        check_geographic_selection(None, None, None, None, 41.9, None, None, None)
    with pytest.raises(ValueError, match="together"):
        check_geographic_selection(None, None, None, None, None, 12.5, None, None)


def test_geographic_guard_rejects_radius_without_centre():
    with pytest.raises(ValueError, match="need latitude and longitude"):
        check_geographic_selection(None, None, None, None, None, None, 10.0, None)


def _epochs(n: int) -> list[StationEpoch]:
    return [StationEpoch(network="IV", station=f"S{i:03d}") for i in range(n)]


def test_paginate_first_page():
    page, pg = paginate(_epochs(120), limit=50, offset=0)
    assert [e.station for e in page][:2] == ["S000", "S001"]
    assert pg == Pagination(
        total_count=120, returned_count=50, limit=50, offset=0, has_more=True, next_offset=50
    )


def test_paginate_middle_and_last_page():
    _, pg = paginate(_epochs(120), limit=50, offset=50)
    assert (pg.returned_count, pg.has_more, pg.next_offset) == (50, True, 100)
    page, pg = paginate(_epochs(120), limit=50, offset=100)
    assert len(page) == 20
    assert (pg.returned_count, pg.has_more, pg.next_offset) == (20, False, None)


def test_paginate_exact_fit_has_no_more():
    _, pg = paginate(_epochs(50), limit=50, offset=0)
    assert pg.has_more is False and pg.next_offset is None


def test_paginate_offset_past_end_is_empty_but_total_is_exact():
    page, pg = paginate(_epochs(10), limit=50, offset=999)
    assert page == []
    assert (pg.total_count, pg.returned_count, pg.has_more) == (10, 0, False)


def test_paginate_empty():
    page, pg = paginate([], limit=50, offset=0)
    assert page == [] and pg.total_count == 0 and pg.has_more is False
