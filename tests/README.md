# Tests

Three layers:

- **`unit/`**: fast, **offline**, deterministic. The text parser on captured
  responses, sorting and pagination, input constraints, the Datacenter registry,
  the text path with `requests` mocked, the response path with ObsPy's `Client`
  mocked and a captured StationXML.
- **`protocol/`**: spawns the real server over stdio and drives it as a client
  would: the legacy `initialize` handshake and the stateless 2026-07-28 envelope
  (`server/discover`, `tools/list`, `tools/call`). Offline: the only tool call
  made fails validation before any request leaves.
- **`integration/`**: **live**, hits INGV, EARTHSCOPE, GFZ and ORFEUS. Marked
  `@pytest.mark.integration`, **excluded by default**, never run in CI.

## Run

```bash
uv run pytest                    # unit + protocol (offline, default)
uv run pytest -m integration     # live, network required
uv run pytest -m ''              # everything
./run_tests.sh [--integration]   # the same inside the Docker image
```

## Fixtures

Captured live on 2026-09-16, never edited by hand: the header quirks the parser
absorbs only exist in real output.

| File | Query | Why it is here |
|---|---|---|
| `ingv_{network,station,channel}.txt` | `IV`, `IV.ACER`, `IV.ACER.HH?` | header with spaces around pipes and lower-case `location` |
| `gfz_{network,station,channel}.txt` | `GE`, `GE.APE`, `GE.APE.BH?` | compact header, upper-case `M/S` units |
| `orfeus_{network,station,channel}.txt` | `NL`, `NL.HGN`, `NL.HGN.BH?` | compact header |
| `earthscope_{network,station,channel}.txt` | `IU`, `IU.ANMO`, `IU.ANMO.BH?` | spaces around pipes, trailing space in the station-level header, four-decimal times, `8.48699E8` scales |
| `ingv_station_iv_a.txt` | `IV.A*` at station level | 36 Epochs for sort and slice tests |
| `ingv_bad_window.error.txt` | `IV` with `starttime` after `endtime` | INGV's verbatim HTTP 400 body |
| `ingv_iv_acer_hhz_response.xml` | `IV.ACER.*.HHZ` at response level | StationXML with three Channel Epochs and full response stages |
| `gfz_ge_ape_bhz_response.xml`, `orfeus_nl_hgn_bhz_response.xml`, `earthscope_iu_anmo_bhz_response.xml` | one `BHZ` Epoch of `GE.APE`, `NL.HGN.02`, `IU.ANMO.00`, window 2024-01-01/02, captured 2026-09-21 | StationXML from the other three advertised Datacenters: units spelled `M/S`/`COUNTS`, `m/s`/`COUNTS`, `m/s`/`counts`; 5, 2 and 3 response stages |

## Conventions

- Unit tests never touch the network: patch `client.requests.get` or `client.Client`.
- A new Datacenter quirk gets a captured fixture and an offline parser test, plus
  a live test documenting the real behaviour under `integration/`.
