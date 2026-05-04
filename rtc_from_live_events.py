#!/usr/bin/env python3
"""
Walk the Live Events export row-by-row (file order). For each Live Events.ID:

  Live Events.ID = FF__INCIDENT__C  ->  FF__APPLICATION__C = Applications.ID

Three checks (all must pass to emit one output row):

  1. Some bridge row has FF__INCIDENT__C equal to this Live Events.ID.
  2. That bridge row has a non-empty FF__APPLICATION__C (one bridge row per incident
     is expected; if several rows share the same FF__INCIDENT__C, the first in the
     bridge file wins).
  3. That FF__APPLICATION__C value exists as Applications.ID.

CS export column names use double underscores (see COL_* constants). Application
columns come from Applications; live-event columns come from the current Live
Events row. Missing non-ID fields may be blank.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# CS CSV column names (as exported)
COL_APP_CMDB = "CMDB_APM_NUMBER__C"
COL_LE_INCIDENT_DT = "FF__INCIDENT_DATE_TIME__C"
COL_LE_RTC = "DETERMINED_RECOVERY_TIME_CAPABILITY_RTC__C"
COL_BRIDGE_APP = "FF__APPLICATION__C"
COL_BRIDGE_INC = "FF__INCIDENT__C"


def _normalize_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"No header row in {path}")
        rows: List[Dict[str, str]] = []
        for row in reader:
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
        return rows


def _build_apps_index(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        k = _normalize_id(r.get("ID"))
        if not k:
            continue
        out[k] = r
    return out


def _build_incident_to_bridge_row(
    bridge_rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    """
    FF__INCIDENT__C -> full bridge row. First occurrence wins if duplicates exist.
    """
    m: Dict[str, Dict[str, str]] = {}
    for r in bridge_rows:
        inc_id = _normalize_id(r.get(COL_BRIDGE_INC))
        if not inc_id or inc_id in m:
            continue
        m[inc_id] = r
    return m


def _cell(row: Optional[Dict[str, str]], key: str) -> str:
    if not row:
        return ""
    v = row.get(key, "")
    if v is None:
        return ""
    return str(v).strip()


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Join Live Events -> bridge (FF__INCIDENT__C / FF__APPLICATION__C) -> "
            "Applications; one output row per Live Events row when all checks pass."
        )
    )
    p.add_argument(
        "applications_csv",
        type=Path,
        help="Applications export (ID, NAME, CMDB_APM_NUMBER__C, ...)",
    )
    p.add_argument(
        "live_events_csv",
        type=Path,
        help="Live Events export (ID, FF__INCIDENT_DATE_TIME__C, "
        "DETERMINED_RECOVERY_TIME_CAPABILITY_RTC__C, NAME, ...)",
    )
    p.add_argument(
        "live_event_applications_csv",
        type=Path,
        help="Bridge export (FF__APPLICATION__C, FF__INCIDENT__C)",
    )
    p.add_argument(
        "output_csv",
        type=Path,
        help="Path for the generated CSV",
    )
    args = p.parse_args()

    apps_rows = _read_csv_rows(args.applications_csv)
    live_rows = _read_csv_rows(args.live_events_csv)
    bridge_rows = _read_csv_rows(args.live_event_applications_csv)

    apps_by_id = _build_apps_index(apps_rows)
    incident_to_bridge = _build_incident_to_bridge_row(bridge_rows)

    fieldnames = [
        "Application.ID",
        "Application.NAME",
        "Application.CMDB_APM_NUMBER_C",
        "Live Event.NAME",
        "Live Event.DETERMINED_RECOVERY_TIME_CAPABILITY_RTC_C",
        "Live Event.FF_INCIDENT_DATE_TIME_C",
    ]

    out_count = 0
    with args.output_csv.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()

        for le_row in live_rows:
            le_id = _normalize_id(le_row.get("ID"))
            if not le_id:
                continue

            # Check 1: bridge links FF__INCIDENT__C to this Live Events.ID
            bridge_row = incident_to_bridge.get(le_id)
            if bridge_row is None:
                continue

            # Check 2: same bridge row has FF__APPLICATION__C
            app_id = _normalize_id(bridge_row.get(COL_BRIDGE_APP))
            if not app_id:
                continue

            # Check 3: FF__APPLICATION__C exists in Applications.ID
            app = apps_by_id.get(app_id)
            if app is None:
                continue

            writer.writerow(
                {
                    "Application.ID": app.get("ID", ""),
                    "Application.NAME": app.get("NAME", ""),
                    "Application.CMDB_APM_NUMBER_C": app.get(COL_APP_CMDB, ""),
                    "Live Event.NAME": _cell(le_row, "NAME"),
                    "Live Event.DETERMINED_RECOVERY_TIME_CAPABILITY_RTC_C": _cell(
                        le_row, COL_LE_RTC
                    ),
                    "Live Event.FF_INCIDENT_DATE_TIME_C": _cell(
                        le_row, COL_LE_INCIDENT_DT
                    ),
                }
            )
            out_count += 1

    print(f"Wrote {out_count} rows to {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
