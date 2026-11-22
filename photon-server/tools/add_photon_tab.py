"""Add (or refresh) a "Photon" tab in a shared Zelos layout, with the Photon actions pre-placed.

    python tools/add_photon_tab.py                 # layout "High Noon"
    python tools/add_photon_tab.py --layout "My Layout" --dry-run

Zelos opens an action dragged from the Explorer as a short panel at the bottom of the tab, and nothing
lets an extension choose that height. Shipping the panels in the team layout means nobody has to build
or stretch them: Load Session fills the left side at full height, List Sessions and Job Status sit on
the right. Re-running replaces only the tab named "Photon"; other tabs are untouched. Every update makes
a new layout version, so `agent.layouts.restore(id, version)` undoes it.

Action panel format (from the Zelos 26.0.10 app): {kind: "ACTION", id, title, location: {x, y, w, h},
namespace, actionName, options: {actionAgent, columnCount, inlineExecute, ...}}. The grid is 12 wide;
heights are layout rows.
"""

from __future__ import annotations

import argparse
import json
import uuid

import zelos_sdk

TAB_NAME = "Photon"
NAMESPACE = "Photon"  # = extension.toml name, the actions' namespace
FULL_HEIGHT = 3.5      # layout rows; about one screen in the app

# Stable ids so re-running the script updates the same tab and panels instead of adding new ones.
_NS = uuid.UUID("6f1b0c2e-3d5a-4b8e-9c7f-0a1b2c3d4e5f")


def _id(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def action_panel(key: str, action: str, title: str, x: float, y: float, w: float, h: float,
                 columns: int = 1, inline: bool = False) -> dict:
    return {"kind": "ACTION", "id": _id(key), "title": title, "location": {"x": x, "y": y, "w": w, "h": h},
            "namespace": NAMESPACE, "actionName": action,
            "options": {"actionAgent": None, "columnCount": columns, "inlineExecute": inline}}


def photon_tab() -> dict:
    half = FULL_HEIGHT / 2
    return {
        "id": _id("tab"),
        "kind": "LAYOUT",
        "name": TAB_NAME,
        "layout": {"panels": [
            action_panel("load", "load_session", "Load a Photon session", 0, 0, 7, FULL_HEIGHT),
            action_panel("list", "list_sessions", "Photon sessions", 7, 0, 5, half),
            action_panel("status", "job_status", "Load progress", 7, half, 5, half, inline=True),
        ]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="High Noon")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    agent = zelos_sdk.connect()
    match = [lay for lay in agent.layouts.list() if lay.name == args.layout]
    if len(match) != 1:
        raise SystemExit(f"expected one layout named {args.layout!r}, found {len(match)}")
    layout = agent.layouts.show(match[0].id)
    data = layout.data if isinstance(layout.data, dict) else json.loads(layout.data)
    tabs = [t for t in data.get("tabs", []) if t.get("name") != TAB_NAME]
    data = {**data, "tabs": [*tabs, photon_tab()]}
    if args.dry_run:
        print(json.dumps(photon_tab(), indent=1))
        return
    agent.layouts.update(layout.id, data=data)
    print(f"{args.layout}: tabs now {[t['name'] for t in data['tabs']]}")


if __name__ == "__main__":
    main()
