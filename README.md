# LHRS Zelos Extensions

[Zelos](https://zeloscloud.io) extensions for Longhorn Racing Solar.

| Folder | Extension | What it does |
|---|---|---|
| [`photon-server/`](photon-server/) | **Photon** | Browse the car sessions stored on the Photon telemetry server and load any of them into Zelos Cloud (tagged `Photon`) to view, share and analyze with Zelos AI |

---

## Photon: install

**You need:**
- the [Zelos desktop app](https://zeloscloud.io), 26.0.10 or newer, signed in to the **lhrs** org (UT email; ask a lead for an invite),
- the Photon web password (ask in #software).

1. **Get the code.**
   ```
   git clone https://github.com/lhr-solar/zelos-extensions.git
   ```
   Keep the folder: Zelos runs the extension from it, and a later `git pull` updates it.
2. **Install it in Zelos.** Click the **Extensions** icon in the left rail. Then, in the **MARKETPLACE** header, open **⋮**, choose **Install Local Extension**, and pick the **`zelos-extensions/photon-server`** folder. The first install sets up Python, which takes under a minute. Photon then shows as **Local**.
3. **Configure it.** Open a live workspace (the one connected to `localhost`) and click the **Explorer** icon (the first icon in the left rail, the one that shows SIGNALS). Under **EXTENSIONS**, click the **gear** on the **Photon** row. Enter the **Photon password**, leave the rest at its defaults, and click **Save & Start** (or **Save & Restart**).
4. **Check it's running.** The Photon row shows a red stop square while it runs, and **ACTIONS** now lists `Photon`.

## Photon: usage guide

**The easy way: the Photon tab.** Open the shared **High Noon** layout (right sidebar → **LAYOUTS** → **High Noon**, `Ctrl+J` if the sidebar is hidden) and pick its **Photon** tab. It has a full-height **Load Session** form, **Photon sessions** (List Sessions) and **Load progress** (Job Status) already in place.

Actions only run in a **live** workspace, the one connected to `localhost`. While a trace is the active data source they show *"Only available in live mode"*. To run them again, switch the active data source back to `localhost` (right sidebar → **DATA SOURCES**), or reopen the live workspace from the title dropdown.

You can also run any action from **ACTIONS → Photon** in the Explorer: double-click one, or drag it onto a layout, to open it in an Action panel, then click **Execute**.

### 1. Find a session
Run **List Sessions**. It shows the stored car sessions grouped by day (newest first), with start time, length and whether video exists. You can also ask Zelos AI, for example *"List the Photon sessions from September"*. It runs this action itself.

### 2. Open a session
Run **Load Session**. It has five fields:
- **Day**, then **Session**: the dropdowns list what Photon has.
- **Resolution**: `1 s` for a whole session, `100 ms` for a few minutes of detail, `raw` only for short windows.
- **Start offset / Duration** (minutes, optional): load just part of a run.

By default it uploads the session to **Zelos Cloud** as a trace **tagged `Photon`**, opens it in Zelos, and returns the share link. Anyone in the lhrs org can open that link, and Zelos AI can analyze the trace.

**Signals use the same names as the CAN extension** (`CAN` → `CarCAN/<Message>` → `<Signal>`), so the shared **High Noon** layout and any other layout built on live PCAN data work on Photon traces unchanged. Live-workspace loads are the exception: they appear under `photon-<session id>` so they never mix with a running CAN extension.

- **Load the same thing again** (same session, resolution, window and signals) and this laptop reopens the trace it already uploaded instead of making a duplicate. To force a fresh trace, use **Load Session (Advanced)** → **Upload again**.
- **Load Session (Advanced)** has the other options: a **Signals** subset (`Message.Signal` names), **Trace name**, **Extra tags** (existing org tags next to `Photon`), **Open in Zelos when done**, **Upload again**, **Allow large transfers**, and **Destination**.
- Zelos can't search the Cloud for existing traces, so a teammate loading the same session gets their own copy. Before loading, check console2 → Traces (filter by the `Photon` tag) for an existing one.

**Other destinations** (the **Destination** field in Load Session (Advanced)):
- **Trace file**: saves a private `.trz` to **Documents/Photon Traces**. Open it with **Open trace** on Home, or **DATA SOURCES → Add trace → File** in the right sidebar (`Ctrl+J`).
- **Live workspace (recent data only)**: streams into the live view, but only for data from the last 7.5 hours (Zelos drops older live data within a minute).

**PDU channels and battery taps come pre-split.** Some CAN messages carry one slot of an array per frame: the PDU reports one of 16 channels at a time, and the BPS reports one of 32 voltage or temperature taps. Every Photon trace also contains these split per slot, with the same sources the live PDU notebook writes, so **High Noon → LV Power Draw** and the **Array** tab's MPPT Power plots work on Photon traces too:

| Source | Events | Fields |
|---|---|---|
| `PDUChannels_LilShah` | `Channel_00`–`Channel_15` | enabled, fault, voltage (V), current (A) |
| `PDUChannelPower_LilShah` | `Channel_00`–`Channel_15` | power (W) = voltage × current |
| `PDUChannelOverall_LilShah` | `Overall` | total_power (W), highest_output, highest_channel_index |
| `BPSVoltageTaps_LilShah` | `Tap_00`–`Tap_31` | voltage (V), fault, age (ms) |
| `BPSVoltageOverall_LilShah` | `Overall` | min/max_voltage, spread, min_tap, max_tap, faulted_taps, taps_reporting |
| `BPSTemperatureTaps_LilShah` | `Tap_00`–`Tap_31` | temperature (°C), fault, age (ms) |
| `BPSTemperatureOverall_LilShah` | `Overall` | the same summary for temperature |
| `BPSTempADCTaps_LilShah` | `Tap_00`–`Tap_31` | adc (raw thermistor reading) |
| `MPPTPower_LilShah` | `Input`, `Output` | MPPT_A, MPPT_B, MPPT_C (W) = voltage × current |

Channel names and fault labels come from the lilShah DBC. MPPT power is computed from voltage and current in the same raw frame, so the derived data lives inside the trace. Notebooks that write derived data with old timestamps put it in the live workspace, where Zelos deletes it within a minute. These messages are always read from Photon's raw data, because its 1 s and 100 ms data averages the channel index across slots. At those resolutions each channel or tap keeps its last value per second (or per 100 ms).

### Battery: live and historical

High Noon's **Battery** tab shows the pack (voltage, average temperature, precharge voltages), the lowest and highest cell voltage and temperature with their taps, how many taps are faulted, the BPS fault and contactor states, the eight segment statuses, min/max trends, and tables of all 32 tap voltages, temperatures and faults.

- **Historical:** load any session with Photon. The trace already carries everything the tab needs, with status codes shown as DBC labels ("Closed", "Overtemperature").
- **Live:** with the CAN extension running, open the **BPS Tap Live Splitter LilShah DBC** notebook (in `photon-server/notebooks/`; copy it into your *Documents/Zelos Notebooks* folder) and run it. It splits the live battery arrays into the same sources Photon traces use and keeps going in the background, so the same tab fills in live. The PDU Channel Live Splitter does the same for LV Power Draw.

### 3. Long jobs
Load Session answers within 4 minutes. Anything longer keeps running in the background. Run **Job Status** to see progress and the final result, or **Cancel Job** to stop.

## Troubleshooting

| Problem | Fix |
|---|---|
| Actions fail with *"Photon password is not set"* or *"refused the password"* | Re-enter the password under EXTENSIONS → Photon → gear, then **Save & Restart** |
| Load fails with *"no 'Photon' tag"* | An lhrs org owner must create the `Photon` tag once in console2.zeloscloud.io → Tags |
| Load fails with a sign-in or storage error | Sign in to the lhrs org in Zelos. If the org's Cloud storage is full, ask an owner, or use Destination **Trace file** |
| A reused trace was deleted from the Cloud | Load again with **Load Session (Advanced)** → **Upload again** |
| LV Power Draw, MPPT Power or other PDU/battery panels empty on a Photon trace | Traces loaded before these sources were added don't have them. Load again; the extension won't reuse those older traces |
| High Noon panels are empty on a Photon trace | Check that the extension's **CAN source name** / **CAN bus name** settings match your CAN extension (defaults `CAN` / `CarCAN`). Traces uploaded before this naming (signals under `photon/`) need **Load Session (Advanced)** → **Upload again** |
| *"roughly N GB of Photon data"* | Pick a coarser resolution or a shorter window, or tick **Allow large transfers** |
| No EXTENSIONS or ACTIONS section | You're viewing a trace. Switch to the live workspace connected to `localhost` |
| A signal stops early at `1 s` / `100 ms` | Photon's rollup data can end before its raw data. Load that window at `raw` |
| Extension shows an amber warning | It crashed. Open its details (click the name) → menu next to **Uninstall** → **View log** |

## Updating

```
git pull
```

Then restart the extension: the red square, then the green play button on the Photon row. If `extension.toml` changed, run **Install Local Extension** again on the same folder.

## Development

Code, design notes and tests are in [`photon-server/README.md`](photon-server/README.md).
