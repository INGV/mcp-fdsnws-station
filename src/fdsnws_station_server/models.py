"""Input constraints and output models of the FDSNWS Station MCP tools.

Inputs are flat tool parameters, so the constraints live as reusable `Annotated`
types that the tool signatures reference: the SDK builds the input schema from
the signature and enforces them before the tool body runs. Only the cross-field
guard (`check_geographic_selection`) runs in the body.

Outputs are one Pydantic model per tool, never a union: on mcp 2.x a single
model annotation becomes the tool's `outputSchema` and its fields the
`structuredContent`, whereas a union is wrapped in a synthetic `{"result": ...}`
object that hides every field and unit from the client. Upstream failures are
therefore an in-band `error` block on the same model.

Field names carry the unit (`elevation_m`, `azimuth_deg`, `sample_rate_hz`)
because the text format's 17 channel columns have no unit in their header and
the schema is the only place a client can read it from.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pydantic_core
from pydantic import AfterValidator, BaseModel, Field

# --- Input constraints -------------------------------------------------------

# Everything the specification allows in a code selection: comma lists, `*` and
# `?` wildcards, and `--` for a blank Location code. The pattern is what keeps
# the value safe to interpolate into the upstream query string; the bound only
# keeps the GET URL short. Measured live on INGV (2026-09-17): the request line
# is capped at 8 KiB by both the backend (431) and nginx (414), so about 1580
# station codes fit. 1024 per code field leaves the four fields plus every
# other parameter under that cap and covers about 200 stations per call.
CODE_PATTERN = r"^[A-Za-z0-9*?,-]+$"
CODE_MAX_LENGTH = 1024

# Exact codes for `get_response`: one Network, one Station, one Channel, no list
# and no wildcard, so the returned Inventory is bounded (a `*` at response level
# can be tens of megabytes of StationXML).
EXACT_CODE_PATTERN = r"^[A-Za-z0-9-]+$"

# The cap is set by the context window of the model reading the page, not by
# the Datacenter. Measured on qwen3.8:27b (tests/evals/calibrate_density.py,
# 2026-09-23), the SDK's indented text block costs 2.16 bytes per token for
# channel Epochs; the widest seen (EarthScope, 549 bytes in a page) is 254
# tokens, so 70 of them are about 18k tokens, 55% of a 32k window, leaving room
# for the conversation. Station (~130 tokens) and network Epochs are cheaper,
# so the same cap holds at every Level. The earlier cap of 500 channel Epochs
# was about 122k tokens and overflowed a 32k window in an evaluation run.
LIMIT_DEFAULT = 50
LIMIT_MAX = 70

# `get_response` has no page to shrink: one exact channel returns every Epoch it
# ever had, each with its full stage list. The response tree tokenizes at 2.79
# bytes per token on qwen3.8:27b (same calibration), so 54000 bytes is about 19k
# tokens, 59% of a 32k window. That admits one Epoch at each of the four
# advertised Datacenters (the largest fixture Epoch is under 23 kB) and both
# Epochs of GE.APE..BHZ (48 kB), and refuses the three of IV.ACER..HHZ (66 kB,
# 23k tokens, 71% of the window in a single call).
RESPONSE_MAX_BYTES = 54_000


def _check_iso8601(value: str) -> str:
    """Structural check only: the value must parse as an ISO 8601 date or
    datetime. It is forwarded verbatim; whether the window makes sense
    (`starttime` before `endtime`) is the Datacenter's call and its error comes
    back verbatim."""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"'{value}' is not an ISO 8601 date or datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
        ) from None
    return value


Code = Annotated[str, Field(pattern=CODE_PATTERN, max_length=CODE_MAX_LENGTH)]
ExactCode = Annotated[str, Field(pattern=EXACT_CODE_PATTERN, max_length=CODE_MAX_LENGTH)]
IsoTime = Annotated[str, AfterValidator(_check_iso8601)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
RadiusKm = Annotated[float, Field(ge=0.0)]
Limit = Annotated[int, Field(ge=1, le=LIMIT_MAX)]
Offset = Annotated[int, Field(ge=0)]


def check_geographic_selection(
    minlatitude: float | None,
    maxlatitude: float | None,
    minlongitude: float | None,
    maxlongitude: float | None,
    latitude: float | None,
    longitude: float | None,
    minradiuskm: float | None,
    maxradiuskm: float | None,
) -> None:
    """The one cross-field rule the tools enforce themselves: a bounding box and
    a radial search are two different selections and the specification does not
    define their combination, so they are rejected here rather than left to
    whatever each Datacenter does with both. A radius without a centre is
    rejected for the same reason. Raises ValueError."""
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be given together for a radial search")
    has_radius = minradiuskm is not None or maxradiuskm is not None
    has_bbox = any(v is not None for v in (minlatitude, maxlatitude, minlongitude, maxlongitude))
    if has_bbox and (latitude is not None or has_radius):
        raise ValueError(
            "bounding-box parameters (minlatitude, maxlatitude, minlongitude, maxlongitude) "
            "and radial parameters (latitude, longitude, minradiuskm, maxradiuskm) "
            "are mutually exclusive"
        )
    if has_radius and latitude is None:
        raise ValueError("minradiuskm/maxradiuskm need latitude and longitude as the centre")


# --- Epochs (one line of format=text) ----------------------------------------

_TIME_DESC = "UTC, verbatim from the Datacenter (ISO 8601)"


class NetworkEpoch(BaseModel):
    """One Network Epoch: a line of `level=network` text output."""

    # Normalised text header (stripped, lower-cased) -> field. The parser maps by
    # name so the three header spellings seen live (INGV with spaces, GFZ
    # compact, EarthScope with a trailing space at station level) all land on the
    # same fields.
    TEXT_COLUMNS: ClassVar[dict[str, str]] = {
        "network": "network",
        "description": "description",
        "starttime": "start_time",
        "endtime": "end_time",
        "totalstations": "total_stations",
    }
    SORT_KEY: ClassVar[tuple[str, ...]] = ("network", "start_time")

    network: str = Field(description="FDSN network code")
    description: str | None = Field(default=None, description="Network description")
    start_time: str | None = Field(default=None, description=f"Epoch start, {_TIME_DESC}")
    end_time: str | None = Field(
        default=None, description=f"Epoch end, {_TIME_DESC}; null while the Epoch is open"
    )
    total_stations: int | None = Field(
        default=None, description="Number of Stations associated with this Network entry"
    )


class StationEpoch(BaseModel):
    """One Station Epoch: a line of `level=station` text output."""

    TEXT_COLUMNS: ClassVar[dict[str, str]] = {
        "network": "network",
        "station": "station",
        "latitude": "latitude",
        "longitude": "longitude",
        "elevation": "elevation_m",
        "sitename": "site_name",
        "starttime": "start_time",
        "endtime": "end_time",
    }
    SORT_KEY: ClassVar[tuple[str, ...]] = ("network", "station", "start_time")

    network: str = Field(description="FDSN network code")
    station: str = Field(description="FDSN station code")
    latitude: float | None = Field(default=None, description="Station latitude, degrees (WGS84)")
    longitude: float | None = Field(default=None, description="Station longitude, degrees (WGS84)")
    elevation_m: float | None = Field(default=None, description="Station elevation, metres")
    site_name: str | None = Field(default=None, description="Site name")
    start_time: str | None = Field(default=None, description=f"Epoch start, {_TIME_DESC}")
    end_time: str | None = Field(
        default=None, description=f"Epoch end, {_TIME_DESC}; null while the Epoch is open"
    )


class ChannelEpoch(BaseModel):
    """One Channel Epoch: a line of `level=channel` text output."""

    TEXT_COLUMNS: ClassVar[dict[str, str]] = {
        "network": "network",
        "station": "station",
        "location": "location",
        "channel": "channel",
        "latitude": "latitude",
        "longitude": "longitude",
        "elevation": "elevation_m",
        "depth": "depth_m",
        "azimuth": "azimuth_deg",
        "dip": "dip_deg",
        "sensordescription": "sensor_description",
        "scale": "scale",
        # The specification's column template says ScaleFrequency, but its own
        # example and every Datacenter probed (INGV, GFZ, ORFEUS, EarthScope)
        # emit ScaleFreq. Both spellings map to the same field.
        "scalefreq": "scale_frequency_hz",
        "scalefrequency": "scale_frequency_hz",
        "scaleunits": "scale_units",
        "samplerate": "sample_rate_hz",
        "starttime": "start_time",
        "endtime": "end_time",
    }
    SORT_KEY: ClassVar[tuple[str, ...]] = (
        "network",
        "station",
        "location",
        "channel",
        "start_time",
    )

    network: str = Field(description="FDSN network code")
    station: str = Field(description="FDSN station code")
    location: str = Field(description="Location code; empty string for a blank code")
    channel: str = Field(description="FDSN channel code (e.g. HHZ)")
    latitude: float | None = Field(default=None, description="Channel latitude, degrees (WGS84)")
    longitude: float | None = Field(default=None, description="Channel longitude, degrees (WGS84)")
    elevation_m: float | None = Field(default=None, description="Channel elevation, metres")
    depth_m: float | None = Field(
        default=None, description="Depth of the sensor below the local surface, metres"
    )
    azimuth_deg: float | None = Field(
        default=None, description="Sensor azimuth, degrees clockwise from north"
    )
    dip_deg: float | None = Field(
        default=None, description="Sensor dip, degrees down from horizontal (-90 = vertical up)"
    )
    sensor_description: str | None = Field(default=None, description="Sensor type/description")
    scale: float | None = Field(
        default=None,
        description=(
            "Total sensitivity (InstrumentSensitivity value), valid at "
            "scale_frequency_hz; dividing counts by it yields data in scale_units"
        ),
    )
    scale_frequency_hz: float | None = Field(
        default=None, description="Frequency at which scale is valid, Hz"
    )
    scale_units: str | None = Field(
        default=None, description="Unit of the data after applying scale (e.g. m/s, m/s**2)"
    )
    sample_rate_hz: float | None = Field(default=None, description="Sample rate, Hz")
    start_time: str | None = Field(default=None, description=f"Epoch start, {_TIME_DESC}")
    end_time: str | None = Field(
        default=None, description=f"Epoch end, {_TIME_DESC}; null while the Epoch is open"
    )


# --- Tool results --------------------------------------------------------------


class Pagination(BaseModel):
    """Client-side paging over the complete, sorted result.

    The specification has no `limit`/`offset`/`orderby`, so the page is cut
    here after the whole response has been downloaded and sorted.
    """

    total_count: int = Field(description="Epochs matched by the Datacenter, before slicing")
    returned_count: int = Field(description="Epochs in this page")
    limit: int
    offset: int = Field(description="0-based index of the first Epoch of this page")
    has_more: bool
    next_offset: int | None = Field(
        default=None, description="offset to request the next page; null on the last page"
    )


def rendered_size(result: BaseModel) -> int:
    """Bytes of the text block the SDK renders for a returned model, which is
    the text the model reads. Mirrors mcp/server/mcpserver/utilities/
    func_metadata.py (`pydantic_core.to_json(result, fallback=str, indent=2)`):
    a size taken from `model_dump_json()` would leave out the indentation, which
    is more than half of a nested response tree."""
    return len(pydantic_core.to_json(result, fallback=str, indent=2))


def paginate(epochs: list, limit: int, offset: int) -> tuple[list, Pagination]:
    """Slice an already sorted list and describe the slice. `has_more` is exact,
    not a heuristic, because the whole result set is in hand."""
    total = len(epochs)
    page = epochs[offset : offset + limit]
    has_more = offset + len(page) < total
    return page, Pagination(
        total_count=total,
        returned_count=len(page),
        limit=limit,
        offset=offset,
        has_more=has_more,
        next_offset=offset + len(page) if has_more else None,
    )


class DatacenterError(BaseModel):
    """An upstream failure, reported in-band so the call stays a readable result."""

    status: int | None = Field(description="HTTP status, or null for a network failure")
    message: str = Field(description="The Datacenter's response body, verbatim")


class QueryResult(BaseModel):
    """Common envelope of the three text-level query tools."""

    datacenter: str = Field(description="Resolved Datacenter name")
    api_url: str = Field(description="The upstream request actually sent")
    query: dict[str, str | float | bool] = Field(
        description="Parameters sent upstream, with upstream names (radii in degrees)"
    )
    pagination: Pagination | None = Field(default=None, description="null when error is set")
    error: DatacenterError | None = Field(
        default=None, description="Set on upstream HTTP >= 400 or network failure"
    )
    message: str | None = Field(default=None, description="Set when no Epoch matched")


class NetworkQueryResult(QueryResult):
    level: Literal["network"] = "network"
    epochs: list[NetworkEpoch]


class StationQueryResult(QueryResult):
    level: Literal["station"] = "station"
    epochs: list[StationEpoch]


class ChannelQueryResult(QueryResult):
    level: Literal["channel"] = "channel"
    epochs: list[ChannelEpoch]


class ResponseResult(BaseModel):
    """Envelope of `get_response`: typed metadata around an untyped StationXML tree."""

    datacenter: str = Field(description="Resolved Datacenter name")
    api_url: str = Field(description="The upstream request actually sent")
    found: bool
    network: str
    station: str
    location: str
    channel: str
    channel_epochs_count: int = Field(description="Channel Epochs in the Inventory")
    inventory: dict | None = Field(
        default=None,
        description=(
            "ObsPy Inventory as a JSON tree (networks > stations > channels > response "
            "with instrument sensitivity and stages); null when not found or when "
            "omitted for size (see message)"
        ),
    )
    error: DatacenterError | None = Field(
        default=None, description="Set on upstream HTTP >= 400 or network failure"
    )
    message: str | None = Field(
        default=None,
        description=(
            "Set when found is false and error is null, or when the inventory was "
            "omitted for size, with the Epochs to narrow to"
        ),
    )
