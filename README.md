# LHRS Zelos Extensions

[Zelos](https://zeloscloud.io) extensions for Longhorn Racing Solar.

| Folder | Extension | What it does |
|---|---|---|
| [`photon-server/`](photon-server/) | **Photon** | Browse the car sessions stored on the Photon telemetry server, open them in Zelos, and upload them to Zelos Cloud (tagged `Photon`) to share and analyze with Zelos AI |

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

Every action runs from **ACTIONS → Photon** in the Explorer. Double-click one, or drag it onto a layout, to open it in an Action panel, then click **Execute**.

### 1. Find a session
Run **List Sessions**. It shows the stored car sessions grouped by day (newest first), with start time, length and whether video exists. You can also ask Zelos AI, for example *"List the Photon sessions from September"*. It runs this action itself.

### 2. Open a session
Run **Load Session**:
- **Day**, then **Session**: the dropdowns list what Photon has.
- **Resolution**: `1 s` for a whole session (a 3.6 h run loads in about 20 s), `100 ms` for a few minutes of detail, `raw` only for short windows.
- **Start offset / Duration** (minutes, optional): load just part of a run.
- **Signals** (optional): `Message.Signal` names, comma-separated, to load only some signals.

It saves a `.trz` file to **Documents/Photon Traces** and returns its path. Open the file in Zelos: **Open trace** on Home, or **DATA SOURCES → Add trace → File** in the right sidebar (`Ctrl+J`). Signals appear as `photon/<Message>.<Signal>`.

> Why a file? Zelos keeps live data for only about 8 hours, so loading weeks-old car data straight into the live view would vanish within a minute. Destination **Live workspace (recent data only)** works only for data from the last 7.5 hours, like today's drive.

### 3. Share a session
Run **Upload Session**. It uses the same choices, plus an optional **Trace name** and **Extra tags**. It uploads to Zelos Cloud **always tagged `Photon`**, opens the trace in Zelos, and returns the share link. Anyone in the lhrs org can open it, and Zelos AI can analyze it.

### 4. Long jobs
Load and Upload answer within 4 minutes. Anything longer keeps running in the background. Run **Job Status** to see progress and the final result, or **Cancel Job** to stop.

## Troubleshooting

| Problem | Fix |
|---|---|
| Actions fail with *"Photon password is not set"* or *"refused the password"* | Re-enter the password under EXTENSIONS → Photon → gear, then **Save & Restart** |
| Upload fails with *"no 'Photon' tag"* | An lhrs org owner must create the `Photon` tag once in console2.zeloscloud.io → Tags |
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
