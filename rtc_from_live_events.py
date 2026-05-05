#!/usr/bin/env python3
"""
Emit one row per Applications CSV row (file order).

Mapping:
  Applications.ID -> Live Event Applications.FF__APPLICATION__C
  -> FF__INCIDENT__C -> Live Events.ID

If no bridge or no matching Live Events rows, the three Application columns are
still written; the three Live Event columns are blank.

If multiple Live Events link to one application:
  - Prefer the chronologically latest FF__INCIDENT_DATE_TIME__C row that has a
    usable DETERMINED_RECOVERY_TIME_CAPABILITY_RTC__C (non-blank, not N/A-like).
  - If the latest-by-datetime row has no usable RTC, try the next latest, and so on.
  - If no linked row has usable RTC, use only the row with the latest incident
    datetime (tie-break: higher Live Events.ID).

CS CSV column names use double underscores (COL_* constants).
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _build_live_events_index(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        k = _normalize_id(r.get("ID"))
        if not k:
            continue
        out[k] = r
    return out


def _build_app_to_incidents(rows: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """FF__APPLICATION__C -> FF__INCIDENT__C values (order preserved; duplicates kept)."""
    m: Dict[str, List[str]] = {}
    for r in rows:
        app_id = _normalize_id(r.get(COL_BRIDGE_APP))
        inc_id = _normalize_id(r.get(COL_BRIDGE_INC))
        if not app_id or not inc_id:
            continue
        m.setdefault(app_id, []).append(inc_id)
    return m


def _parse_incident_datetime(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sort_key_latest_first(inc_id: str, le: Dict[str, str]) -> Tuple[float, str]:
    """Higher tuple sorts later for max(); we take max() for 'latest'."""
    dt = _parse_incident_datetime(le.get(COL_LE_INCIDENT_DT))
    if dt is None:
        return (float("-inf"), inc_id)
    return (dt.timestamp(), inc_id)


def _rtc_is_blank_or_na(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    return s.casefold() in {
        "n/a",
        "na",
        "-",
        "--",
        "none",
        "null",
        "tbd",
        "tbc",
    }


def _has_usable_rtc(le: Dict[str, str]) -> bool:
    return not _rtc_is_blank_or_na(le.get(COL_LE_RTC))


def _choose_live_event(
    resolved: List[Tuple[str, Dict[str, str]]],
) -> Optional[Dict[str, str]]:
    """
    resolved: (Live Events.ID, live_row) for rows found in the Live Events file.
    Returns one live row or None if resolved is empty.
    """
    if not resolved:
        return None

    # Dedupe by incident id; keep first occurrence order from bridge for ties.
    seen: set[str] = set()
    uniq: List[Tuple[str, Dict[str, str]]] = []
    for inc_id, le in resolved:
        if inc_id in seen:
            continue
        seen.add(inc_id)
        uniq.append((inc_id, le))

    # Latest incident datetime first (max timestamp; tie-break higher inc_id).
    sorted_pairs = sorted(
        uniq,
        key=lambda t: _sort_key_latest_first(t[0], t[1]),
        reverse=True,
    )

    for _inc_id, le in sorted_pairs:
        if _has_usable_rtc(le):
            return le

    # No usable RTC anywhere: use strictly latest by incident datetime.
    _best_iid, latest_le = max(
        uniq,
        key=lambda t: _sort_key_latest_first(t[0], t[1]),
    )
    return latest_le


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
            "Applications -> bridge -> Live Events; one output row per application; "
            "pick one live event per app when multiple links exist."
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

    live_by_id = _build_live_events_index(live_rows)
    app_to_incidents = _build_app_to_incidents(bridge_rows)

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

        for app in apps_rows:
            app_id = _normalize_id(app.get("ID"))
            if not app_id:
                continue

            incident_ids = app_to_incidents.get(app_id, [])
            unique_incident_ids = list(dict.fromkeys(incident_ids))

            resolved: List[Tuple[str, Dict[str, str]]] = [
                (iid, live_by_id[iid])
                for iid in unique_incident_ids
                if iid in live_by_id
            ]

            chosen_le: Optional[Dict[str, str]] = None
            if resolved:
                chosen_le = _choose_live_event(resolved)

            writer.writerow(
                {
                    "Application.ID": app.get("ID", ""),
                    "Application.NAME": app.get("NAME", ""),
                    "Application.CMDB_APM_NUMBER_C": app.get(COL_APP_CMDB, ""),
                    "Live Event.NAME": _cell(chosen_le, "NAME"),
                    "Live Event.DETERMINED_RECOVERY_TIME_CAPABILITY_RTC_C": _cell(
                        chosen_le, COL_LE_RTC
                    ),
                    "Live Event.FF_INCIDENT_DATE_TIME_C": _cell(
                        chosen_le, COL_LE_INCIDENT_DT
                    ),
                }
            )
            out_count += 1

    print(f"Wrote {out_count} rows to {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
