"""Datacenter access: name registry, `format=text` path, StationXML path.

Two paths, chosen by the level requested:

- network, station and channel levels go through `format=text` with `requests`.
  The whole response is parsed into Epoch models and sorted here, because the
  specification has no `limit`/`offset`/`orderby` and a network at channel level
  is thousands of lines. ObsPy is bypassed on this path: its client validates
  parameters against each Datacenter's WADL, and this server has no
  per-Datacenter behaviour by design: the Datacenter decides, its body is
  returned verbatim.
- response level goes through ObsPy `Client(base_url=...)`, which is the only
  StationXML parser worth having, and the resulting Inventory is serialised to a
  JSON tree.

Nothing in here is Datacenter-specific. A Datacenter is always a name resolved
through `resolve_datacenter`, never a URL chosen by the caller: in HTTP mode a
caller-supplied URL would turn this server into an open proxy.
"""

import asyncio
import logging
import os
import re
from enum import Enum
from urllib.parse import urlencode

import requests
from obspy import Inventory, UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import URL_MAPPINGS, FDSNException, FDSNNoDataException
from pydantic import ValidationError

from .models import ChannelEpoch, NetworkEpoch, StationEpoch

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45.0

# Kilometres per degree of great-circle arc on a spherical Earth of mean radius
# 6371 km (2 * pi * 6371 / 360). The specification takes radii in degrees;
# callers reason in kilometres, and converting here keeps the query portable
# across Datacenters. The spherical approximation is off by
# well under a kilometre at the scale of a station search.
KM_PER_DEGREE = 111.19

EPOCH_MODELS = {
    "network": NetworkEpoch,
    "station": StationEpoch,
    "channel": ChannelEpoch,
}


class DatacenterRequestError(Exception):
    """An upstream failure: HTTP >= 400 (with the Datacenter's body verbatim) or a
    network error (with the exception text). `status` is None for the latter.
    Carries the request that failed, so the tool result can still show it."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        api_url: str = "",
        query: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.api_url = api_url
        self.query = query or {}


# --- Datacenter registry: names only, never caller URLs -----------------------


def parse_datacenters(raw: str) -> dict[str, str]:
    """Parse `NAME=https://base.url,NAME2=http://host:port` into {NAME: base_url}.

    Names are upper-cased so lookups are case-insensitive; base URLs lose a
    trailing slash so `/fdsnws/station/1/query` can be appended uniformly. A
    malformed entry raises ValueError: the variable is read once at import, so a
    typo fails the server at startup instead of at the first call.
    """
    registry: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, url = entry.partition("=")
        name, url = name.strip(), url.strip()
        if not sep or not name or not url.startswith(("http://", "https://")):
            raise ValueError(f"FDSN_DATACENTERS entry {entry!r} is not NAME=http(s)://base.url")
        registry[name.upper()] = url.rstrip("/")
    return registry


CONFIGURED_DATACENTERS = parse_datacenters(os.environ.get("FDSN_DATACENTERS", ""))
DEFAULT_DATACENTER = os.environ.get("FDSN_DEFAULT_DATACENTER", "INGV").strip().upper()


def available_datacenters() -> list[str]:
    """Every resolvable name: configured ones first, then ObsPy's registry."""
    obspy_names = sorted(k.upper() for k in URL_MAPPINGS)
    return sorted(CONFIGURED_DATACENTERS) + [
        n for n in obspy_names if n not in CONFIGURED_DATACENTERS
    ]


def resolve_datacenter(name: str | None) -> tuple[str, str]:
    """Return (canonical NAME, base_url) for a Datacenter name, or the default
    when None. A configured name shadows an ObsPy one so an operator can
    redirect even `INGV` to a mirror. Unknown name -> ValueError listing every
    available name, which is what the model needs to correct the call."""
    key = (name or DEFAULT_DATACENTER).strip().upper()
    if key in CONFIGURED_DATACENTERS:
        return key, CONFIGURED_DATACENTERS[key]
    for obspy_name, url in URL_MAPPINGS.items():
        if obspy_name.upper() == key:
            return key, url.rstrip("/")
    raise ValueError(f"Unknown datacenter '{key}'. Available: {', '.join(available_datacenters())}")


# Fail at startup, not at the first call, if the configured default is unknown.
resolve_datacenter(None)


def _timeout() -> float:
    raw = os.environ.get("FDSN_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid FDSN_TIMEOUT=%r, using default %s", raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT


# --- Text path ------------------------------------------------------------------


def upstream_params(params: dict) -> dict:
    """Turn tool parameters into the query string actually sent: drop unset
    ones, convert kilometre radii to the specification's degrees, and omit
    `includerestricted` at its specification default (TRUE)."""
    sent: dict = {}
    for key, value in params.items():
        if value is None:
            continue
        if key in ("minradiuskm", "maxradiuskm"):
            sent[key[:-2]] = round(value / KM_PER_DEGREE, 6)
        elif key == "includerestricted":
            if value is False:
                sent[key] = "false"
        else:
            sent[key] = value
    return sent


def build_query_url(base_url: str, level: str, sent: dict) -> str:
    return f"{base_url}/fdsnws/station/1/query?" + urlencode(
        {"format": "text", "level": level, **sent}
    )


def _cell(value: str) -> str | None:
    """An empty cell is a missing value (open EndTime, no description), not an
    empty string. The one exception is Location, handled by the caller: a blank
    Location code is legitimately the empty string."""
    value = value.strip()
    return value if value else None


def parse_text(raw: str, level: str) -> list:
    """Parse a `format=text` body into Epoch models of the given level.

    Header-driven, not positional: the first `#` line is normalised (each cell
    stripped and lower-cased) and mapped to model fields by name, so INGV's
    `#Network | Station | location`, GFZ's `#Network|Station|Location` and
    EarthScope's trailing-space header all parse alike. A column the model does
    not know is ignored; a missing one leaves the field None, except the code
    columns, without which a line is not an Epoch. A body with no header, or a
    line that does not fit the model, raises ValueError: that is a Datacenter
    answering 200 with something other than the text format (an HTML page from
    a misconfigured private service, say), and the caller reports it as such.
    """
    model = EPOCH_MODELS[level]
    fields: list[str | None] = []
    epochs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            if not fields:
                header = [c.strip().lower() for c in line[1:].split("|")]
                fields = [model.TEXT_COLUMNS.get(c) for c in header]
            continue
        if not fields:
            raise ValueError(f"not a format=text body (no # header): {line[:120]!r}")
        cells = line.split("|")
        data: dict = {}
        for field, cell in zip(fields, cells, strict=False):
            if field is None:
                continue
            data[field] = cell.strip() if field == "location" else _cell(cell)
        try:
            epochs.append(model(**data))
        except ValidationError as e:
            raise ValueError(f"line does not fit level={level}: {line[:120]!r} ({e})") from None
    return epochs


def sort_epochs(epochs: list, level: str) -> list:
    """Order by the level's natural key (Network, Station, Location, Channel,
    StartTime, whichever the level has) so page boundaries do not depend on the
    Datacenter's own order, which INGV, GFZ and EarthScope do not share."""
    key_fields = EPOCH_MODELS[level].SORT_KEY
    return sorted(epochs, key=lambda e: tuple(getattr(e, f) or "" for f in key_fields))


async def query_text(level: str, params: dict, datacenter: str | None) -> tuple[list, str, dict]:
    """Fetch and parse one level of text output from a Datacenter.

    Returns (sorted epochs, api_url, parameters sent). HTTP 204 and 404 are the
    specification's two "no data" codes and yield an empty list; any other
    status >= 400 and any network failure raise DatacenterRequestError.
    """
    _, base_url = resolve_datacenter(datacenter)
    sent = upstream_params(params)
    api_url = build_query_url(base_url, level, sent)
    logger.info("GET %s", api_url)
    try:
        resp = await asyncio.to_thread(requests.get, api_url, timeout=_timeout())
    except requests.RequestException as e:
        raise DatacenterRequestError(str(e), api_url=api_url, query=sent) from e
    if resp.status_code in (204, 404):
        return [], api_url, sent
    if resp.status_code >= 400:
        raise DatacenterRequestError(
            resp.text.strip() or f"HTTP {resp.status_code}",
            status=resp.status_code,
            api_url=api_url,
            query=sent,
        )
    try:
        epochs = parse_text(resp.text, level)
    except ValueError as e:
        raise DatacenterRequestError(
            f"Unparseable response: {e}", status=resp.status_code, api_url=api_url, query=sent
        ) from e
    return sort_epochs(epochs, level), api_url, sent


# --- Response path ----------------------------------------------------------------


async def get_response(
    network: str,
    station: str,
    location: str,
    channel: str,
    starttime: str | None,
    endtime: str | None,
    datacenter: str | None,
) -> tuple[Inventory, str]:
    """Fetch `level=response` StationXML through ObsPy for exact codes.

    Returns (Inventory, api_url); "no data" (204, and 404 as on the text path)
    is an empty Inventory, any other FDSN failure raises DatacenterRequestError
    with ObsPy's message, which embeds the Datacenter's body, and the HTTP
    status when ObsPy knows it. `Client(base_url=...)` reads the WADL, so a
    private Datacenter must be a full implementation, WADL included. Times are
    handed to ObsPy as the strings the caller gave, so `api_url` shows them
    verbatim.
    """
    _, base_url = resolve_datacenter(datacenter)
    kwargs: dict = {
        "network": network,
        "station": station,
        "location": location,
        "channel": channel,
        "level": "response",
    }
    if starttime is not None:
        kwargs["starttime"] = starttime
    if endtime is not None:
        kwargs["endtime"] = endtime
    api_url = f"{base_url}/fdsnws/station/1/query?" + urlencode(kwargs)
    logger.info("GET %s (via ObsPy)", api_url)

    def fetch() -> Inventory:
        client = Client(base_url=base_url, timeout=_timeout())
        return client.get_stations(**kwargs)

    try:
        inventory = await asyncio.to_thread(fetch)
    except FDSNNoDataException:
        return Inventory(), api_url
    except FDSNException as e:
        # ObsPy types the common statuses (400, 401, 403, 429, 500, ...) and
        # leaves the rest as "Unknown HTTP code: N"; 404 is the specification's
        # other "no data" code and must not surface as a failure.
        status = e.status_code
        if status is None:
            m = re.search(r"Unknown HTTP code: (\d+)", str(e))
            status = int(m.group(1)) if m else None
        if status == 404:
            return Inventory(), api_url
        raise DatacenterRequestError(str(e).strip(), status=status, api_url=api_url) from e
    except Exception as e:  # noqa: BLE001
        # Not everything ObsPy raises is an FDSNException: lxml raises
        # XMLSyntaxError on a 200 whose body is truncated or empty StationXML,
        # and Client() raises ValueError when the host does not answer at all.
        # Only ObsPy runs inside fetch(), so a broad catch here masks none of
        # our own bugs, and it turns a hidden "Error executing tool" into the
        # same in-band error the text path reports for an unparseable body.
        raise DatacenterRequestError(
            f"Unparseable or unreachable Datacenter: {type(e).__name__}: {e}".strip(),
            api_url=api_url,
        ) from e
    return inventory, api_url


def inventory_to_dict(obj):
    """Recursively convert an ObsPy object tree to JSON-serialisable values.

    ObsPy keeps most Inventory attributes as `_name` behind a `name` property
    (`_networks`, `_code`, `_latitude`, `_zeros`), so a private key whose class
    exposes the public property is emitted under the public name; anything else
    private is dropped, as are None values. UTCDateTime and Enum become strings,
    complex poles and zeros become {"real", "imag"}, numpy scalars and arrays
    become numbers and lists. Units and values stay as StationXML has them:
    nothing is derived.
    """
    if obj is None or isinstance(obj, bool):
        return obj
    # Plain builtins first: ObsPy subclasses float/complex to attach
    # uncertainties, and JSON wants the bare value.
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, complex):
        return {"real": obj.real, "imag": obj.imag}
    if isinstance(obj, str):
        return str(obj)
    if isinstance(obj, (UTCDateTime, Enum)):
        return str(obj)
    module = type(obj).__module__
    if module == "numpy" or module.startswith("numpy."):
        return obj.item() if getattr(obj, "shape", None) == () else obj.tolist()
    if isinstance(obj, (list, tuple)):
        return [inventory_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        items = list(obj.items())
    elif hasattr(obj, "__dict__"):
        items = []
        for key, value in vars(obj).items():
            public = key.lstrip("_")
            if key.startswith("_") and isinstance(getattr(type(obj), public, None), property):
                key = public
            items.append((key, value))
    else:
        return str(obj)
    result = {}
    for key, value in items:
        if not isinstance(key, str) or key.startswith("_"):
            continue
        converted = inventory_to_dict(value)
        if converted is not None:
            result[key] = converted
    return result or None
