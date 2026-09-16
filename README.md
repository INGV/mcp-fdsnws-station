[![Build Status](https://github.com/INGV/mcp-fdsnws-station/actions/workflows/docker-build-push.yml/badge.svg?branch=main)](https://github.com/INGV/mcp-fdsnws-station/actions/workflows/docker-build-push.yml?query=branch%3Amain)
[![Tests](https://github.com/INGV/mcp-fdsnws-station/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/INGV/mcp-fdsnws-station/actions/workflows/tests.yml?query=branch%3Amain)
[![Version](https://img.shields.io/badge/dynamic/yaml?label=ver&query=softwareVersion&url=https://raw.githubusercontent.com/INGV/mcp-fdsnws-station/main/publiccode.yml)](https://github.com/INGV/mcp-fdsnws-station/blob/main/publiccode.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/ingv/mcp-fdsnws-station)](https://hub.docker.com/r/ingv/mcp-fdsnws-station)
[![License](https://img.shields.io/github/license/INGV/mcp-fdsnws-station.svg)](https://github.com/INGV/mcp-fdsnws-station/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/INGV/mcp-fdsnws-station.svg)](https://github.com/INGV/mcp-fdsnws-station/issues)

# FDSNWS Station MCP Server

An MCP (Model Context Protocol) server for the FDSN Web Service Station API
([fdsnws-station 1.1](https://www.fdsn.org/webservices/fdsnws-station-1.1.pdf)).
It exposes network, station, channel and instrument-response metadata of any
FDSN-compliant datacenter (INGV, EarthScope, GFZ, ORFEUS, or a private intranet
service) to MCP clients such as Claude Desktop and Open WebUI, with a bounded and
predictable context cost.

## Features

- **4 MCP tools**, one per level: `fdsnws_station_query_networks`,
  `fdsnws_station_query_stations`, `fdsnws_station_query_channels` (from FDSN
  `format=text`) and `fdsnws_station_get_response` (from StationXML through ObsPy)
- **Typed output**: one Epoch object per line of text output, with the unit in the
  field name (`elevation_m`, `azimuth_deg`, `sample_rate_hz`, ...) and a real
  `outputSchema` advertised to the client
- **Server-side ordering and pagination** with an exact `total_count`, because the
  FDSN service has neither
- **Multi-datacenter by name**: ObsPy's registry plus your own `FDSN_DATACENTERS`,
  so an intranet StationXML server is one environment variable away
- **Datacenter-agnostic**: only specification parameters are sent; kilometre radii
  are converted to degrees so radial search works everywhere
- **`mcp` 2.x**: server identity and instructions, cache hints, stdio by default,
  Streamable HTTP without a proxy
- **Containerized**: multi-arch image, non-root user

## Installation

### Prerequisites

- Docker
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (for local development only)

### Option A: Pull from Docker Hub (recommended)

Prebuilt multi-arch images (linux/amd64, linux/arm64) are published on Docker Hub:

```bash
# Latest release
docker pull ingv/mcp-fdsnws-station:latest

# A specific version (replace X.Y.Z with a published tag)
docker pull ingv/mcp-fdsnws-station:X.Y.Z
```

### Option B: Build the container locally

```bash
git clone https://github.com/INGV/mcp-fdsnws-station.git
cd mcp-fdsnws-station
docker build -t ingv/mcp-fdsnws-station .
```

### Option C: Local development

```bash
uv sync                                        # creates .venv from uv.lock
uv run python -m fdsnws_station_server.server  # stdio MCP server
```

## Usage

### Start the MCP server

```bash
# stdio (default): the client spawns the container and talks over stdin/stdout
docker run -i --rm ingv/mcp-fdsnws-station

# Streamable HTTP on port 8000, endpoint http://localhost:8000/mcp
docker run -d --rm -p 8000:8000 -e MCP_TRANSPORT=streamable-http ingv/mcp-fdsnws-station
```

### Configuration

All configuration is by environment variable.

| Variable | Default | Meaning |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | Bind address (`streamable-http` only) |
| `MCP_PORT` | `8000` | Bind port (`streamable-http` only) |
| `FDSN_DEFAULT_DATACENTER` | `INGV` | Datacenter used when a call omits `datacenter` |
| `FDSN_DATACENTERS` | (empty) | Extra datacenters, `NAME=https://base.url,NAME2=http://host:port` (see [Datacenters](#datacenters)) |
| `FDSN_TIMEOUT` | `45` | Upstream request timeout, seconds |
| `REQUESTS_CA_BUNDLE` | (system) | CA bundle for a private TLS authority, text path (`requests`) |
| `SSL_CERT_FILE` | (system) | The same bundle for the response path (ObsPy uses `urllib`) |
| `LOG_LEVEL` | `INFO` | Python log level; logs go to stderr only |

## MCP client configuration

### Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "fdsnws-station": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "ingv/mcp-fdsnws-station"]
    }
  }
}
```

To point it at an intranet datacenter, add the variables to the same command:

```json
"args": ["run", "-i", "--rm",
         "-e", "FDSN_DATACENTERS=RSNI=http://stationxml.intranet:8080",
         "-e", "FDSN_DEFAULT_DATACENTER=RSNI",
         "ingv/mcp-fdsnws-station"]
```

### Open WebUI (Streamable HTTP)

Open WebUI (0.6.31 and later) speaks MCP natively over Streamable HTTP; no `mcpo`
proxy is involved and none is shipped.

1. Run the server in HTTP mode:
   ```bash
   docker run -d --name mcp-fdsnws-station -p 8000:8000 \
     -e MCP_TRANSPORT=streamable-http ingv/mcp-fdsnws-station
   ```
2. In Open WebUI go to **Settings → Tools** (or, for all users, **Admin Panel →
   Settings → Tools**), add a tool server of type **MCP (Streamable HTTP)** with the
   URL `http://<host>:8000/mcp`.
3. If Open WebUI itself runs in Docker, `localhost` from inside its container will
   not reach the host: use `http://host.docker.internal:8000/mcp` (Docker Desktop),
   the host's LAN address, or put both containers on the same Docker network and use
   the container name.

The server is stateless in HTTP mode (every request is self-contained, JSON
responses). It has **no authentication and no rate limiting of its own**, and in
the container it binds `0.0.0.0`. That is fine on a workstation or inside a trusted
network; exposing it further means a reverse proxy in front that terminates TLS
and enforces authentication and rate limits. These are properties of the
deployment, not of the server, which is read-only, stateless and holds no
credentials. In stdio mode it opens no port at all.

## Tools

All tools accept an optional `datacenter` name (default: `FDSN_DEFAULT_DATACENTER`,
normally `INGV`). The typical flow is networks → stations → channels → response.

Code parameters (`network`, `station`, `location`, `channel`) accept comma-separated
lists and the wildcards `*` and `?`, and `--` for a blank location code, exactly as
the FDSN specification defines them; they are forwarded verbatim. Time parameters are
ISO 8601 UTC dates or datetimes (`2024-01-01` or `2024-01-01T00:00:00`), forwarded
verbatim; there is **no default time window**, so a query without one returns every
Epoch ever operated.

### `fdsnws_station_query_networks`

`level=network`. Parameters: `network`, `starttime`, `endtime`, `startbefore`,
`startafter`, `endbefore`, `endafter`, `updatedafter`, `includerestricted`
(default `true`), `limit`, `offset`, `datacenter`.

### `fdsnws_station_query_stations`

`level=station`. Parameters: those of `query_networks` plus `station`, `location`,
`channel`, a bounding box (`minlatitude`, `maxlatitude`, `minlongitude`,
`maxlongitude`) or a radial search (`latitude`, `longitude`, `minradiuskm`,
`maxradiuskm`). Bounding box and radial search are mutually exclusive; `latitude`
and `longitude` must be given together, and a radius needs them as its centre.

### `fdsnws_station_query_channels`

`level=channel`. Same parameters as `query_stations`. A whole network at channel
level is thousands of Epochs (INGV `IV`: about 7500), which is what pagination is
for; narrow by station or channel when you can.

### `fdsnws_station_get_response`

`level=response` through StationXML. Parameters: `network`, `station`, `channel`
(**required, exact codes**: no list, no wildcard), `location` (default `*`, may be a
wildcard or `--`), `starttime`, `endtime`, `datacenter`. Returns the full ObsPy
`Inventory` as a JSON tree: networks → stations → channels → `response` with
`instrument_sensitivity` and every `response_stages` entry (poles and zeros as
`{real, imag}` pairs, coefficients, FIR, decimation, gains). Values and units are
StationXML's own; nothing is derived. Use a time window to select a single Epoch
when the channel changed instrument over time.

### Output fields and units

Every query tool returns one object:

```
datacenter   resolved name (e.g. "INGV")
level        "network" | "station" | "channel"
api_url      the upstream request actually sent
query        parameters sent upstream, with upstream names (radii in degrees)
pagination   {total_count, returned_count, limit, offset, has_more, next_offset}; null on error
epochs       list of Epoch objects for the level (below)
error        {status, message} on upstream failure, else null
message      hint when no Epoch matched, else null
```

One Epoch is one line of FDSN text output. Codes and times are verbatim strings,
numbers are floats, an empty cell is `null`, and an open Epoch has `end_time: null`.

| Level | Fields |
|---|---|
| network | `network`, `description`, `start_time`, `end_time`, `total_stations` |
| station | `network`, `station`, `latitude` (deg), `longitude` (deg), `elevation_m`, `site_name`, `start_time`, `end_time` |
| channel | `network`, `station`, `location`, `channel`, `latitude` (deg), `longitude` (deg), `elevation_m`, `depth_m`, `azimuth_deg`, `dip_deg`, `sensor_description`, `scale`, `scale_frequency_hz`, `scale_units`, `sample_rate_hz`, `start_time`, `end_time` |

At channel level, `scale` is the total sensitivity (StationXML
`InstrumentSensitivity`), valid at `scale_frequency_hz`; `scale_units` is the unit of
the data after the scale is applied (e.g. `m/s`, `m/s**2`). `depth_m` is the sensor
depth below the local surface; `azimuth_deg` is clockwise from north and `dip_deg`
is down from horizontal (`-90` = vertical, pointing up).

`fdsnws_station_get_response` returns `datacenter`, `api_url`, `found`, the four
codes requested, `channel_epochs_count`, `inventory` (the JSON tree, `null` when not
found), `error` and `message`.

### Errors and empty results

An upstream failure (HTTP 4xx/5xx, a network error, or a 200 whose body is not the
FDSN text format, such as an HTML page from a misconfigured private service) is
**not** a protocol error: the tool result carries `error: {status, message}` with
the datacenter's response body verbatim, and `pagination` is `null`. A query that matches nothing is not an
error either: `total_count` is `0` and `message` says so. Invalid input (a wildcard
in `get_response`, a bounding box together with a radial search, a malformed time,
an unknown datacenter name) is rejected before any request leaves, with the reason
in the tool error text; an unknown datacenter name lists every available name.

## Pagination and ordering

**The FDSN station service has no `limit`, `offset` or `orderby`. Pagination and
ordering are features of this server**, not of the datacenter, so do not look for
them in a datacenter's WADL.

For every query the server downloads the complete `format=text` response, sorts the
Epochs by `network, station, location, channel, start_time` (whichever the level
has), and returns the slice `[offset, offset + limit)`. `limit` defaults to 50 and is
capped at 500; `offset` is 0-based. `pagination.total_count` is exact and
`has_more`/`next_offset` are derived from it, not guessed.

Consequences: page boundaries do not depend on the datacenter's own order (INGV,
GFZ and EarthScope do not order the same way), and **every page re-downloads the
full result**. The cap exists for the client's context budget, not for bandwidth:
the second page of a 7500-Epoch query costs the datacenter as much as the first.

## Radial search in kilometres

The specification defines `minradius`/`maxradius` in **degrees**. The tools take
`minradiuskm`/`maxradiuskm` in **kilometres**, because that is what people reason
in, and convert them before sending: `degrees = km / 111.19` (great-circle arc on a
spherical Earth of mean radius 6371 km). The request that reaches the datacenter
uses the specification's `minradius`/`maxradius`, so it works on GFZ, ORFEUS,
EarthScope and any private service, not only on INGV (whose `maxradiuskm` is a
local extension and is never sent).

The conversion is spherical, not ellipsoidal: at the scale of a station search the
error is well under a kilometre. The degree values actually sent are visible in
`api_url` and `query`.

## Parameters deliberately not exposed

The tools expose only parameters defined in fdsnws-station 1.1 Table 1, and among
those only the ones the advertised datacenters honour. Not exposed:

- `includeavailability`, `matchtimeseries`: INGV documents them as not implemented
  and silently ignored; a filter that no-ops would mislead the model.
- `format`, `nodata`, `level`: internal to the implementation.
- `minradiuskm`/`maxradiuskm` **as upstream parameters**: an INGV extension, see
  above for how kilometres are handled.
- INGV's `format=json` / `format=geojson`: extensions, not portable.

Validation is structural only (types, ranges, ISO 8601 syntax, the bounding-box
versus radial guard). Whether `starttime` precedes `endtime`, whether a code exists,
how a wildcard is matched: those are the datacenter's decisions, and its error body
is returned verbatim in `error.message`. `includerestricted` and `updatedafter` are
exposed because both are in the specification and work on INGV.

## Datacenters

A datacenter is always addressed by a **name**, never by a URL chosen by the
caller (in HTTP mode a raw URL would turn the server into an open proxy).

Names resolve first against `FDSN_DATACENTERS`, then against ObsPy's registry
(`obspy.clients.fdsn.header.URL_MAPPINGS`); a configured name shadows an ObsPy one,
so an operator can redirect even `INGV` to a mirror. Names are case-insensitive.
`FDSN_DEFAULT_DATACENTER` (default `INGV`) is used when a call omits `datacenter`.

Advertised public datacenters, all verified live:

| Name | Base URL |
|---|---|
| `INGV` (default) | `https://webservices.ingv.it` |
| `EARTHSCOPE` | `https://service.earthscope.org` |
| `GFZ` | `https://geofon.gfz.de` |
| `ORFEUS` | `https://www.orfeus-eu.org` |

`USGS` and `EMSC` have **no station service** (HTTP 404) and are not advertised.
Any other ObsPy name (`ETH`, `RESIF`, `NOA`, ...) is accepted and its response
passed through.

### A private intranet datacenter

An intranet StationXML server that implements fdsnws-station 1.1 (including the
WADL, which ObsPy reads for the response level) is added with one variable. Base
URLs are the part before `/fdsnws/station/1/query`.

```bash
docker run -i --rm \
  -e FDSN_DATACENTERS="RSNI=http://stationxml.intranet:8080,LAB=https://lab.example.org" \
  -e FDSN_DEFAULT_DATACENTER=RSNI \
  ingv/mcp-fdsnws-station
```

With this, a call without `datacenter` goes to `RSNI`; `datacenter: "LAB"` and
`datacenter: "GFZ"` still work; the server fails at startup on a malformed entry or
on a default name it cannot resolve.
If `lab.example.org` uses a certificate from a private CA, mount the bundle and
point both HTTP stacks at it, no code involved: `REQUESTS_CA_BUNDLE=/certs/ca.pem`
for the text path (`requests`) and `SSL_CERT_FILE=/certs/ca.pem` for the response
path (ObsPy uses `urllib`, whose default SSL context reads `SSL_CERT_FILE`). The `api_url` echoed in every result shows the configured host,
which is intended: it is the operator's own deployment.

## Example queries

```
"Which networks does INGV serve? List the ones still active."
"Find the stations of network IV within 30 km of L'Aquila."
"List the HH? channels of station IV.ACER with their sensitivities."
"Give me the instrument response of IV.ACER..HHZ in 2022."
"Same query on GFZ: channels of GE.APE."
```

## Development

### Project structure

```
mcp-fdsnws-station/
├── src/fdsnws_station_server/
│   ├── __init__.py        # __version__ from package metadata
│   ├── server.py          # MCPServer, the four tools, transport selection
│   ├── models.py          # input constraints, Epoch and result models
│   └── client.py          # datacenter registry, text path, response path, serialisation
├── tests/
│   ├── fixtures/          # format=text captured from INGV, GFZ, ORFEUS, EarthScope; StationXML
│   ├── unit/              # offline: parser, sort/slice, models, registry, error mapping
│   ├── protocol/          # spawns the server over stdio: initialize, server/discover, tools/*
│   ├── integration/       # live, opt-in (@pytest.mark.integration)
│   └── README.md
├── pyproject.toml, uv.lock
├── Dockerfile, run_tests.sh
└── .github/workflows/     # tests.yml (offline, 3.11-3.13), docker-build-push.yml
```

### Testing

```bash
./run_tests.sh                 # build image, ruff, unit + protocol (offline)
./run_tests.sh --integration   # ... plus live tests on INGV, EARTHSCOPE, GFZ, ORFEUS

# Without Docker
uv run ruff check src tests
uv run pytest                  # offline (default)
uv run pytest -m integration   # live
```

The live suite is never run in CI. Test details and fixture provenance in
[`tests/README.md`](tests/README.md).

### Manual protocol check

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | docker run -i --rm ingv/mcp-fdsnws-station
```

The legacy `initialize` handshake (protocol 2024-11-05 and 2025-x clients such as
Claude Desktop) is still answered.

### CI / Release

`tests.yml` runs ruff and the offline suite on Python 3.11, 3.12 and 3.13 from
`uv.lock`. `docker-build-push.yml` builds the multi-arch image and pushes it to
`ingv/mcp-fdsnws-station`: `main` → `main`, `develop` → `develop`, tag `vX.Y.Z` →
`X.Y.Z` and `latest`; pull requests build only. On a tag push a retention step keeps
the five latest semver versions.

### FDSN API

- **Specification**: [fdsnws-station 1.1](https://www.fdsn.org/webservices/fdsnws-station-1.1.pdf)
- **INGV service**: https://webservices.ingv.it/fdsnws/station/1/
- Query tools use `format=text`; `get_response` uses StationXML via
  [ObsPy](https://docs.obspy.org/)

## Citation

A [`CITATION.cff`](CITATION.cff) file is provided, so GitHub can generate the
citation in APA or BibTeX from the *Cite this repository* button.

## License

This project is released under the **GNU Affero General Public License v3.0 or
later** (AGPL-3.0-or-later). See the [`LICENSE`](LICENSE) file for the full text.

## Authors

See [`AUTHORS.md`](AUTHORS.md).

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch off `develop`
3. Commit your changes (see [Conventional Commits](https://www.conventionalcommits.org/))
4. Open a Pull Request against `develop`

By contributing you agree that your contributions are licensed under the
AGPL-3.0-or-later license of this project.

## Support

For problems or questions, open an issue in the
[GitHub repository](https://github.com/INGV/mcp-fdsnws-station/issues).
