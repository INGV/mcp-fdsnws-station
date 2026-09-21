# Release Notes

### Release 1.0.0-dev (2026-09-21)
  - fix: a non-FDSN exception on the response path (lxml `XMLSyntaxError` on a truncated or empty StationXML body, ObsPy `ValueError` on an unreachable host) is folded into the in-band `error` block, as the unparseable-text case already was; before, it reached the client as the SDK's hidden `Error executing tool`
  - docs: replace references to unversioned ADR and design-document numbers in code comments and docstrings with the rationale stated inline
  - fix: raise the per-field bound on `network`, `station`, `location` and `channel` selections from 64 to 1024 characters; a list of 21 station codes was rejected before reaching the datacenter, while INGV accepts GET URLs up to 8 KiB (about 1580 codes, measured live)
  - Initial implementation: MCP server for the FDSN fdsnws-station 1.1 service on the `mcp` 2.x SDK (protocol revision 2026-07-28), with server identity, instructions, a public one-hour cache hint on the tool list, and read-only tool annotations
  - feat: four tools, one per level. `fdsnws_station_query_networks`, `fdsnws_station_query_stations` and `fdsnws_station_query_channels` fetch `format=text` and return typed Epoch objects with units in the field names (`elevation_m`, `depth_m`, `azimuth_deg`, `dip_deg`, `scale_frequency_hz`, `sample_rate_hz`) and a real `outputSchema`; `fdsnws_station_get_response` returns the full instrument response of one exact channel as a JSON tree of the ObsPy `Inventory`
  - feat: server-side ordering and pagination (`limit` default 50, max 500; 0-based `offset`; exact `total_count`, `has_more`, `next_offset`), since the FDSN service has none
  - feat: radial search in kilometres (`minradiuskm`/`maxradiuskm`) converted to the specification's degrees, so it works on every datacenter and not only on INGV
  - feat: datacenters resolved by name from ObsPy's registry plus an operator-defined `FDSN_DATACENTERS` registry (`NAME=https://base.url,...`), with `FDSN_DEFAULT_DATACENTER`; advertised public datacenters INGV, EARTHSCOPE, GFZ, ORFEUS
  - feat: upstream failures reported in-band (`error` with the datacenter's verbatim body), empty results as `total_count: 0` with a hint, never as protocol errors
  - feat: stdio transport by default, Streamable HTTP (stateless, JSON responses) with `MCP_TRANSPORT=streamable-http`, `MCP_HOST`, `MCP_PORT`; no mcpo wrapper
  - build: `uv` with a committed `uv.lock`, `ruff`, `pytest`; two-stage `python:3.11-slim` image with a non-root user; multi-arch (amd64/arm64) Docker Hub workflow with a five-version retention
  - test: offline unit suite on responses captured from INGV, GFZ, ORFEUS and EarthScope (header quirks, sort and slice, radii, registry, error mapping, StationXML serialisation); protocol suite driving the server over stdio (legacy `initialize` and stateless `server/discover`); opt-in live suite over the four advertised datacenters and a configured alias
