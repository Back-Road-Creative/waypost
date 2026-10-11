"""Long-form performance report: lifetime views and views/day by publish month
and by title class, from the ``performance_updated`` ledger joined to the
site's ``videos.yaml``. Pure functions; no network."""

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional

import yaml

TITLE_CLASSES = [
    ("route | 4K Scenic Drive", re.compile(r"\| 4K Scenic Drive$")),
    ("REGION Scenic Drive in 4K | route", re.compile(r"Scenic Drive in 4K \|")),
    ("POV ...", re.compile(r"^POV\b")),
    ("Highlights from ...", re.compile(r"^Highlights from\b")),
    ("{route} in 4K", re.compile(r"in 4K$")),
]
OTHER_CLASS = "other/legacy"


def classify_title(title: str) -> str:
    for name, pattern in TITLE_CLASSES:
        if pattern.search(title):
            return name
    return OTHER_CLASS


def load_latest_views(ledger: Path) -> Dict[str, dict]:
    """Latest ``performance_updated`` event per ``data.video_id``."""
    latest: Dict[str, dict] = {}
    with open(ledger, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event_type") != "performance_updated":
                continue
            vid = ev.get("data", {}).get("video_id")
            if not vid:
                continue
            prev = latest.get(vid)
            if prev is None or ev["timestamp"] > prev["timestamp"]:
                latest[vid] = ev
    return latest


def load_videos(videos_yaml: Path) -> List[dict]:
    with open(videos_yaml, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["videos"]


def join_rows(videos: Iterable[dict], latest: Dict[str, dict]) -> List[dict]:
    """One row per video that has a ledger entry WITH a views measurement: month,
    title class, views, views/day. An entry whose ``views`` is missing or ``None``
    yields no row — a median over measured values cannot absorb an unknown as 0.
    A measured 0 is a measurement and stays a row."""
    rows = []
    for v in videos:
        ev = latest.get(v["id"])
        if ev is None:
            continue
        published = date.fromisoformat(str(v["date"]))
        synced = datetime.fromisoformat(ev["timestamp"]).date()
        age_days = max((synced - published).days, 1)
        views = ev["data"].get("views")
        if views is None:
            continue
        rows.append(
            {
                "id": v["id"],
                "month": published.strftime("%Y-%m"),
                "title_class": classify_title(v["title"]),
                "views": views,
                "views_per_day": views / age_days,
            }
        )
    return rows


def _table(rows: List[dict], key: str, header: str) -> List[str]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    lines = [
        f"| {header} | n | median lifetime views | median views/day |",
        "|---|---|---|---|",
    ]
    for name, rs in sorted(groups.items()):
        mv = median(r["views"] for r in rs)
        mvd = median(r["views_per_day"] for r in rs)
        cell = name.replace("|", "\\|")
        lines.append(f"| {cell} | {len(rs)} | {mv:.0f} | {mvd:.2f} |")
    return lines


def render_markdown(
    rows: List[dict], synced_at: Optional[str] = None, unmeasured: int = 0
) -> str:
    head = ["# Long-form performance report", ""]
    if synced_at:
        head.append(f"Ledger last sync: {synced_at}  ")
    head.append(f"Videos joined: {len(rows)}  ")
    head.append(f"Videos with a ledger entry but no views measurement: {unmeasured}")
    body = ["", "## By publish month", "", *_table(rows, "month", "publish month")]
    body += ["", "## By title class", "", *_table(rows, "title_class", "title class")]
    return "\n".join(head + body) + "\n"


def build_report(ledger: Path, videos_yaml: Path) -> str:
    latest = load_latest_views(ledger)
    videos = load_videos(videos_yaml)
    rows = join_rows(videos, latest)
    with_entry = sum(1 for v in videos if v["id"] in latest)
    synced = max((e["timestamp"] for e in latest.values()), default=None)
    return render_markdown(rows, synced, unmeasured=with_entry - len(rows))
