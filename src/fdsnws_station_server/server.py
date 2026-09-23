#!/usr/bin/env python3
"""FDSNWS Station MCP Server: FDSN station metadata from any fdsnws-station 1.1 Datacenter.

Four tools, one per Level: three text-level queries with server-side paging
and one response-level fetch through StationXML. Every tool returns exactly
one Pydantic model, so the SDK advertises a real `outputSchema` and an
upstream failure travels in-band as `error`.

Transport is chosen by `MCP_TRANSPORT`: stdio by default, Streamable HTTP
without a proxy otherwise.
"""

import logging
import os
import sys

from mcp.server import MCPServer
from mcp.server.caching import CacheHint
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .client import (
    DatacenterRequestError,
    get_response,
    inventory_to_dict,
    query_text,
    resolve_datacenter,
)
from .models import (
    LIMIT_DEFAULT,
    LIMIT_MAX,
    RESPONSE_MAX_BYTES,
    ChannelQueryResult,
    Code,
    DatacenterError,
    ExactCode,
    IsoTime,
    Latitude,
    Limit,
    Longitude,
    NetworkQueryResult,
    Offset,
    RadiusKm,
    ResponseResult,
    StationQueryResult,
    check_geographic_selection,
    paginate,
    rendered_size,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "FDSN station metadata (fdsnws-station 1.1) from a Datacenter chosen by name. "
    "Typical flow: fdsnws_station_query_networks to find network codes, "
    "fdsnws_station_query_stations to locate stations, fdsnws_station_query_channels "
    "for channel details and sensitivities, fdsnws_station_get_response for the full "
    "instrument response of one exact channel. Results of the query tools are sorted "
    "and paginated by this server (limit/offset, exact total_count); page through with "
    "next_offset. All times are UTC (ISO 8601). Code parameters accept comma-separated "
    "lists and the wildcards * and ?, except network, station and channel in "
    "get_response, which take exact codes. "
    "The datacenter parameter defaults to the operator's configured Datacenter; other "
    "public ones are EARTHSCOPE, GFZ and ORFEUS."
)

mcp = MCPServer(
    name="fdsnws_station_mcp",
    title="FDSNWS Station",
    version=__version__,
    website_url="https://github.com/INGV/mcp-fdsnws-station",
    instructions=INSTRUCTIONS,
    # The tool list is a build-time constant of the image, so a client may cache
    # it for an hour and share that cache: nothing in it depends on who asks.
    cache_hints={"tools/list": CacheHint(ttl_ms=3_600_000, scope="public")},
)

_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

# Parameter documentation shared by the tool descriptions. The SDK derives the
# input schema from the signature, which carries types and ranges but not
# meaning, so the meaning goes here where the model reads it.
_NETWORK_CODE_DOC = "network accepts a comma-separated list and the wildcards * and ?."
_CODES_DOC = (
    "Codes (network, station, location, channel) accept comma-separated lists and the "
    "wildcards * and ?; use -- for a blank location code."
)
_TIME_DOC = (
    "Time parameters are ISO 8601 UTC dates or datetimes, forwarded verbatim: "
    "starttime/endtime select metadata for data on or after starttime and on or before "
    "endtime, "
    "startbefore/startafter/endbefore/endafter constrain the Epoch bounds, "
    "updatedafter selects metadata updated after that time. No default window: "
    "without one, every Epoch ever operated is returned."
)
_PAGING_DOC = (
    f"Results are sorted and paginated by this server: limit (default {LIMIT_DEFAULT}, "
    f"max {LIMIT_MAX}) and 0-based offset; pagination.total_count is exact and next_offset "
    "gives the next page. An upstream failure is reported in the error field."
)
_GEO_DOC = (
    "Geographic selection is either a bounding box (minlatitude, maxlatitude, "
    "minlongitude, maxlongitude) or a radial search (latitude, longitude, "
    "minradiuskm, maxradiuskm; kilometres, converted to degrees before the request is "
    "sent); the two "
    "are mutually exclusive."
)
_DATACENTER_DOC = (
    "datacenter is a name (default: the configured one, normally INGV); other "
    "public Datacenters with a station service: EARTHSCOPE, GFZ, ORFEUS."
)


def _query_params(**params) -> dict:
    """Keep only what the caller set; None never reaches the query string."""
    return {k: v for k, v in params.items() if v is not None}


async def _run_query(
    level: str, result_model, datacenter: str | None, limit: int, offset: int, params: dict
):
    """Shared body of the three query tools: resolve, fetch, page, and fold an
    upstream failure into the result rather than raising."""
    try:
        name, _ = resolve_datacenter(datacenter)
    except ValueError as e:
        raise ToolError(str(e)) from None
    logger.info("query level=%s datacenter=%s params=%s", level, name, params)
    try:
        epochs, api_url, sent = await query_text(level, params, name)
    except DatacenterRequestError as e:
        return result_model(
            datacenter=name,
            api_url=e.api_url,
            query=e.query,
            epochs=[],
            error=DatacenterError(status=e.status, message=e.message),
        )
    page, pagination = paginate(epochs, limit, offset)
    message = None
    if pagination.total_count == 0:
        message = (
            f"No {level} epochs matched at {name}. Check the codes and wildcards, "
            "or widen the time window."
        )
    return result_model(
        datacenter=name,
        api_url=api_url,
        query=sent,
        pagination=pagination,
        epochs=page,
        message=message,
    )


@mcp.tool(
    name="fdsnws_station_query_networks",
    description=(
        "List FDSN networks (level=network) of a Datacenter: code, description, "
        "operating Epoch and total station count. Start here to find network codes. "
        + _NETWORK_CODE_DOC
        + " "
        + _TIME_DOC
        + " "
        + _PAGING_DOC
        + " "
        + _DATACENTER_DOC
    ),
    annotations=_ANNOTATIONS,
)
async def fdsnws_station_query_networks(
    network: Code | None = None,
    starttime: IsoTime | None = None,
    endtime: IsoTime | None = None,
    startbefore: IsoTime | None = None,
    startafter: IsoTime | None = None,
    endbefore: IsoTime | None = None,
    endafter: IsoTime | None = None,
    updatedafter: IsoTime | None = None,
    includerestricted: bool = True,
    limit: Limit = LIMIT_DEFAULT,
    offset: Offset = 0,
    datacenter: str | None = None,
) -> NetworkQueryResult:
    params = _query_params(
        network=network,
        starttime=starttime,
        endtime=endtime,
        startbefore=startbefore,
        startafter=startafter,
        endbefore=endbefore,
        endafter=endafter,
        updatedafter=updatedafter,
        includerestricted=includerestricted,
    )
    return await _run_query("network", NetworkQueryResult, datacenter, limit, offset, params)


@mcp.tool(
    name="fdsnws_station_query_stations",
    description=(
        "List stations (level=station) with coordinates, elevation, site name and "
        "operating Epoch. Filter by codes, time and geography. "
        + _CODES_DOC
        + " "
        + _TIME_DOC
        + " "
        + _GEO_DOC
        + " "
        + _PAGING_DOC
        + " "
        + _DATACENTER_DOC
    ),
    annotations=_ANNOTATIONS,
)
async def fdsnws_station_query_stations(
    network: Code | None = None,
    station: Code | None = None,
    location: Code | None = None,
    channel: Code | None = None,
    starttime: IsoTime | None = None,
    endtime: IsoTime | None = None,
    startbefore: IsoTime | None = None,
    startafter: IsoTime | None = None,
    endbefore: IsoTime | None = None,
    endafter: IsoTime | None = None,
    updatedafter: IsoTime | None = None,
    minlatitude: Latitude | None = None,
    maxlatitude: Latitude | None = None,
    minlongitude: Longitude | None = None,
    maxlongitude: Longitude | None = None,
    latitude: Latitude | None = None,
    longitude: Longitude | None = None,
    minradiuskm: RadiusKm | None = None,
    maxradiuskm: RadiusKm | None = None,
    includerestricted: bool = True,
    limit: Limit = LIMIT_DEFAULT,
    offset: Offset = 0,
    datacenter: str | None = None,
) -> StationQueryResult:
    try:
        check_geographic_selection(
            minlatitude,
            maxlatitude,
            minlongitude,
            maxlongitude,
            latitude,
            longitude,
            minradiuskm,
            maxradiuskm,
        )
    except ValueError as e:
        raise ToolError(str(e)) from None
    params = _query_params(
        network=network,
        station=station,
        location=location,
        channel=channel,
        starttime=starttime,
        endtime=endtime,
        startbefore=startbefore,
        startafter=startafter,
        endbefore=endbefore,
        endafter=endafter,
        updatedafter=updatedafter,
        minlatitude=minlatitude,
        maxlatitude=maxlatitude,
        minlongitude=minlongitude,
        maxlongitude=maxlongitude,
        latitude=latitude,
        longitude=longitude,
        minradiuskm=minradiuskm,
        maxradiuskm=maxradiuskm,
        includerestricted=includerestricted,
    )
    return await _run_query("station", StationQueryResult, datacenter, limit, offset, params)


@mcp.tool(
    name="fdsnws_station_query_channels",
    description=(
        "List channels (level=channel) with location code, coordinates, depth, "
        "orientation (azimuth, dip), sensor description, total sensitivity (scale at "
        "scale_frequency_hz, in scale_units), sample rate and operating Epoch. One "
        "Epoch per line: a channel appears once per instrument change. A whole network "
        "can be thousands of Epochs, so narrow by station or channel when possible. "
        + _CODES_DOC
        + " "
        + _TIME_DOC
        + " "
        + _GEO_DOC
        + " "
        + _PAGING_DOC
        + " "
        + _DATACENTER_DOC
    ),
    annotations=_ANNOTATIONS,
)
async def fdsnws_station_query_channels(
    network: Code | None = None,
    station: Code | None = None,
    location: Code | None = None,
    channel: Code | None = None,
    starttime: IsoTime | None = None,
    endtime: IsoTime | None = None,
    startbefore: IsoTime | None = None,
    startafter: IsoTime | None = None,
    endbefore: IsoTime | None = None,
    endafter: IsoTime | None = None,
    updatedafter: IsoTime | None = None,
    minlatitude: Latitude | None = None,
    maxlatitude: Latitude | None = None,
    minlongitude: Longitude | None = None,
    maxlongitude: Longitude | None = None,
    latitude: Latitude | None = None,
    longitude: Longitude | None = None,
    minradiuskm: RadiusKm | None = None,
    maxradiuskm: RadiusKm | None = None,
    includerestricted: bool = True,
    limit: Limit = LIMIT_DEFAULT,
    offset: Offset = 0,
    datacenter: str | None = None,
) -> ChannelQueryResult:
    try:
        check_geographic_selection(
            minlatitude,
            maxlatitude,
            minlongitude,
            maxlongitude,
            latitude,
            longitude,
            minradiuskm,
            maxradiuskm,
        )
    except ValueError as e:
        raise ToolError(str(e)) from None
    params = _query_params(
        network=network,
        station=station,
        location=location,
        channel=channel,
        starttime=starttime,
        endtime=endtime,
        startbefore=startbefore,
        startafter=startafter,
        endbefore=endbefore,
        endafter=endafter,
        updatedafter=updatedafter,
        minlatitude=minlatitude,
        maxlatitude=maxlatitude,
        minlongitude=minlongitude,
        maxlongitude=maxlongitude,
        latitude=latitude,
        longitude=longitude,
        minradiuskm=minradiuskm,
        maxradiuskm=maxradiuskm,
        includerestricted=includerestricted,
    )
    return await _run_query("channel", ChannelQueryResult, datacenter, limit, offset, params)


@mcp.tool(
    name="fdsnws_station_get_response",
    description=(
        "Get the full instrument response (level=response, from StationXML) of one "
        "exact channel: instrument sensitivity and every response stage (poles and "
        "zeros, coefficients, FIR, decimation, gains) as a JSON tree of the ObsPy "
        "Inventory, one entry per channel Epoch. network, station and channel must be "
        "exact codes (no list, no wildcard); location defaults to * and may be a "
        "wildcard or -- for blank. Narrow with starttime/endtime (ISO 8601 UTC) to a "
        "single Epoch when the channel changed instrument over time. A result over "
        f"{RESPONSE_MAX_BYTES} bytes omits the inventory and lists the Epochs to narrow "
        "to. " + _DATACENTER_DOC
    ),
    annotations=_ANNOTATIONS,
)
async def fdsnws_station_get_response(
    network: ExactCode,
    station: ExactCode,
    channel: ExactCode,
    location: Code = "*",
    starttime: IsoTime | None = None,
    endtime: IsoTime | None = None,
    datacenter: str | None = None,
) -> ResponseResult:
    try:
        name, _ = resolve_datacenter(datacenter)
    except ValueError as e:
        raise ToolError(str(e)) from None
    logger.info("response %s.%s.%s.%s datacenter=%s", network, station, location, channel, name)
    common = {
        "datacenter": name,
        "network": network,
        "station": station,
        "location": location,
        "channel": channel,
    }
    try:
        inventory, api_url = await get_response(
            network, station, location, channel, starttime, endtime, name
        )
    except DatacenterRequestError as e:
        return ResponseResult(
            **common,
            api_url=e.api_url,
            found=False,
            channel_epochs_count=0,
            error=DatacenterError(status=e.status, message=e.message),
        )
    count = sum(len(sta.channels) for net in inventory for sta in net)
    if count == 0:
        return ResponseResult(
            **common,
            api_url=api_url,
            found=False,
            channel_epochs_count=0,
            message=(
                f"No response found at {name} for {network}.{station}.{location}.{channel}. "
                "Codes must be exact: check them with fdsnws_station_query_channels, "
                "or widen the time window."
            ),
        )
    result = ResponseResult(
        **common,
        api_url=api_url,
        found=True,
        channel_epochs_count=count,
        inventory=inventory_to_dict(inventory),
    )
    size = rendered_size(result)
    if size <= RESPONSE_MAX_BYTES:
        return result
    # Too large for the model's context: say so in-band and hand back the Epoch
    # windows, which is what the caller needs to ask again for one of them.
    logger.info("response omitted: %d bytes over %d", size, RESPONSE_MAX_BYTES)
    return result.model_copy(
        update={"inventory": None, "message": _over_limit_message(inventory, count, size)}
    )


def _over_limit_message(inventory, count: int, size: int) -> str:
    head = f"over the {RESPONSE_MAX_BYTES}-byte limit of one result, so inventory is omitted"
    if count == 1:
        return (
            f"The response of this single channel Epoch is {size} bytes, {head}. It cannot "
            "be narrowed further; fdsnws_station_query_channels gives its total sensitivity."
        )
    windows = "; ".join(
        f"{net.code}.{sta.code}.{cha.location_code}.{cha.code} "
        f"{cha.start_date.isoformat()} to {cha.end_date.isoformat() if cha.end_date else 'open'}"
        for net in inventory
        for sta in net
        for cha in sta
    )
    return (
        f"The response of {count} channel Epochs is {size} bytes, {head}. Call again with "
        f"starttime and endtime both inside one of these Epochs: {windows}."
    )


def main() -> None:
    """Entry point: stdio unless MCP_TRANSPORT selects Streamable HTTP.

    Stateless HTTP with JSON responses is what a tool server behind Open WebUI
    needs: every request is self-contained and no session state survives a
    container restart. Logging goes to stderr only, because stdout is the
    protocol channel in stdio mode.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
            stateless_http=True,
            json_response=True,
        )
    elif transport == "stdio":
        mcp.run()
    else:
        raise SystemExit(f"MCP_TRANSPORT must be 'stdio' or 'streamable-http', got {transport!r}")


if __name__ == "__main__":
    main()
