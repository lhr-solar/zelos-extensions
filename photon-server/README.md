# Photon for Zelos

A Zelos extension that brings Longhorn Racing Solar's **Photon** telemetry server into the Zelos app. Use it to:
- browse every car session Photon has stored,
- load any session or window into Zelos Cloud as a trace tagged **Photon**, ready to view, share and analyze with Zelos AI,
- or save it as a private local `.trz` file instead.

It only reads Photon, through the same endpoints photon.html uses. It never changes the shared Photon backend: no source, DBC or timeline changes.

## Actions

| Action | What it does | Zelos AI |
|---|---|---|
| **List Sessions** | Lists Photon's stored sessions, grouped by local day (newest first), with start time, length, samples and whether video exists | Runs without asking (read-only) |
| **Load Session** | Pick a day and a session, plus optional resolution, start offset and duration. Array messages (PDU channels, battery taps) are read raw and also split per channel/tap. It builds the trace, seals it as TRZ2, uploads it to Zelos Cloud **tagged `Photon`** (plus any extra tags), opens it in the app and returns the share link. Loading the same thing again on this laptop reuses that trace. Signals use the CAN extension's names (`CAN` / `CarCAN/<Message>` / `<Signal>`, event type `zelos.can.message.v1`), so CAN layouts such as High Noon work on Photon traces | Asks first |
| **Load Session (Advanced)** | Load Session plus every other option: *Destination* (*Zelos Cloud*, a local *Trace file* in *Documents/Photon Traces*, or the *Live workspace* for recent data), a signal subset, *Allow large transfers*, trace name, extra tags, open in app, *Upload again* | Asks first |
| **Job Status** | Progress and the result of the running or last load/upload | Read-only |
| **Cancel Job** | Stops the running load or upload after its current chunk | Asks first |

**Resolutions** match Photon's TSDB levels:
- `1 s`: whole sessions (3.6 h ≈ 16 s, 38 MB trace).
- `100 ms`: a few minutes of detail (10 min ≈ 5 s).
- `raw`: short windows only (a whole session raw is about 6 GB of CSV; transfers over about 1.5 GB need *Allow large transfers*).
- `10 s`: overviews.

Load Session returns within 4 minutes. A longer job keeps running, and Job Status reports how it finished.

### Why not stream history into the live workspace

The Zelos app keeps live data in memory and **prunes samples older than about 8 hours** (by sample time) every minute. Photon history is weeks or months old, so it would vanish within a minute of loading.

*Destination → Live workspace (recent data only)* exists for data from the last 7.5 hours, such as today's drive. The action refuses older windows.

### Same names as the CAN extension

Zelos layouts bind each signal by `source`, `message` (event), `signal` (field) and `eventType`. The CAN extension writes source `CAN`, events `<bus>/<Message>` and event type `zelos.can.message.v1`, so Photon traces are written the same way. Photon and the HighNoon DBCs use the same message and signal names (checked for the Pedals tab), so one layout serves live PCAN data and Photon traces. Live-workspace loads use their own `photon-<session id>` source so they can't collide with a running CAN extension.

### Array messages (PDU channels, battery taps)

`PDU_Status_Arr` (indexed by `HSS_Channel_idx`, 16 channels) and `BPS_Voltage_Aggregate_Arr`, `BPS_Temperature_Aggregate_Arr` and `BPS_Temp_ADC_Aggregate_Arr` (indexed by `BPS_Tap_idx`, 32 taps) carry one slot per frame. `zelos_extension_photon/arrays.py` handles them:

- **Always read raw.** Photon's rollup levels average every signal in a bucket, index included. A 1 s `HSS_Channel_idx` of 13.5 or 7.58 mixes channels. At the 1 s, 100 ms and 10 s resolutions these messages are fetched separately at raw, in 5-minute pieces, and the averaged rows are discarded.
- **Thinned per slot.** At those resolutions the last frame of each channel or tap per bucket is kept, so traces stay small while every slot stays correct. At raw resolution every frame is kept.
- **Split per slot.** Each trace gets the "PDU Channel Live Splitter LilShah DBC" notebook's sources (`PDUChannels_LilShah`, `PDUChannelPower_LilShah`, `PDUChannelOverall_LilShah`): same fields, DBC labels and Overall math (total of each channel's latest power, highest output). The battery taps follow the same pattern: `BPSVoltageTaps_LilShah`, `BPSTemperatureTaps_LilShah` and `BPSTempADCTaps_LilShah` (`Tap_00`–`Tap_31`), plus `BPSVoltageOverall_LilShah` and `BPSTemperatureOverall_LilShah` (min, max, spread, which taps, faulted taps, taps reporting).
- **Live-workspace loads only thin.** In the live workspace the live notebooks own the `_LilShah` sources, so the extension doesn't write them there.

Raw data also often has several frames of one message at the same timestamp, because Photon stamps a whole CANP batch with one time. Every signal lists those frames in the same order, so the k-th row of each signal at a timestamp is paired into the k-th frame instead of overwriting the earlier ones.

`TRACE_FORMAT` in `loader.py` is part of the upload-record key. Bump it whenever what goes into a trace changes, so laptops stop reusing older traces.

### Reusing uploads

Zelos has no API to list or search Cloud traces, so the extension can't tell whether a teammate already uploaded a session. Instead each laptop keeps a record in `~/.photon-zelos/uploads.json`, keyed by org, signal naming, session, resolution, window and signal list. A repeat load reopens the recorded trace, and *Upload again* bypasses the record. Trace names follow one pattern, `<local date time> <session id> (<resolution>)[ from HH:MM N min]`, so duplicates are easy to spot in the console.

### The Photon tag

Zelos never creates tags on upload. If a Cloud load fails with *"Zelos Cloud has no 'Photon' tag"*, an org owner needs to create the tag once in [console2.zeloscloud.io](https://console2.zeloscloud.io) → Tags.

### The Photon tab in High Noon

Zelos opens an action dragged from the Explorer as a short panel at the bottom of the tab, and no extension or action setting controls that height. So the shared **High Noon** layout ships a **Photon** tab: Load Session at full height on the left, List Sessions and Job Status on the right. `tools/add_photon_tab.py` adds or refreshes that tab in any layout (`--layout NAME`, `--dry-run`). It only replaces the tab named *Photon*, and each run is a new layout version, so `agent.layouts.restore(id, version)` undoes it.

## Settings

| Setting | Default | |
|---|---|---|
| Photon URL | `https://photon-dev.lhrsolar.org` | Base of the Photon web UI |
| Photon password | — | The shared Photon web password (user `photon`). It lives only in this extension's settings, never in the repo |
| Time zone | `America/Chicago` | For day grouping and labels. Data stays UTC |
| Default resolution | `1 s` | |
| CAN source name | `CAN` | Trace source for Photon signals. Match the CAN extension's prefix |
| CAN bus name | `CarCAN` | Bus segment of event names. Match the CAN extension's *Channel Display Name* |
| Trace folder | *Documents/Photon Traces* | Where Load Session writes `.trz` files |
| Zelos organization | `lhrs` | For uploads. Empty means your active org |

## How it works

```
Photon (photon-dev.lhrsolar.org, Basic auth)
  /ws          protocol v1: hello -> catalog_list -> catalog_result      (session list)
  /export.csv  ?session&from&to&level&signals -> t_ms,message,signal,unit,value
        |
        v  1. discover the session's messages/signals at the coarsest level (schemas can't grow later)
        v  2. stream the window in chunks (1 min raw ... 60 min at 1 s), group rows by (message, t_ms)
        v  3. one Zelos event per CAN message, one Float64 field per signal, DBC unit
TraceWriter (.trz, own namespace)
   --Zelos Cloud (default)--> agent seals TRZ2 -> agent.upload(tags=["Photon", ...]) -> record in
                              ~/.photon-zelos/uploads.json -> zelos://open   (a repeat load just reopens it)
   --Trace file-------------> Documents/Photon Traces/<start> <session> <res>.trz
```

## Known data caveats (Photon side)

- Photon's rollup levels can **end earlier than raw data** for some signals. In `20260723-164704-00`, `BPS_Status.Main_Battery_Voltage` ends at 19:09 at 1 s and 20:06 at 100 ms, while raw runs to 20:20. Use `raw` for the window you need, and report it to the Photon owner.
- The newest stored session is 2026-10-17. Ask the Photon owner whether live tail is still writing the TSDB.
- If a result shows `dropped > 0`, Photon had signals at the requested level that the coarse discovery pass didn't see. Those rows were skipped.

## Development

```
python -m venv .venv
.venv\Scripts\pip install -e . pytest ruff
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
```

On Windows, keep the checkout in a short path: pyarrow (a zelos-sdk dependency) fails to load from very long venv paths ("The filename or extension is too long").

To try it against a running Zelos app without installing, put the settings in a JSON file and run:

```
set ZELOS_CONFIG_PATH=C:\path\to\config.json
.venv\Scripts\python main.py
```

The actions show up in the app's Action panel as `Photon/...` while it runs. To install it properly, add this folder as a local extension in the Zelos app (or `zelos extensions install-local <folder>` with the Zelos CLI).

## Roadmap

- **Phase 2:** live car data from the Oracle CANP tee (decoder and parser prototyped in the Phase 0 spike).
- **Live battery split:** a live-CAN counterpart (notebook or shared splitter) for the battery taps, like the PDU notebook.
- **High Noon battery tabs:** voltage and temperature panels on the `BPS…_LilShah` sources.
- **Phase 3:** marketplace release, shared layouts for the Pit, Dashboard and Dynamics screens, and a signal glossary for Zelos AI.
