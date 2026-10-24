"""Long-form performance report: lifetime views and views/day by publish month
and by title class, from the ``performance_updated`` ledger joined to the
site's ``videos.yaml``. Pure functions; no network."""

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

TITLE_CLASSES = [
    ("route | 4K Scenic Drive", re.compile(r"\| 4K Scenic Drive$")),
    ("REGION Scenic Drive in 4K | route", re.compile(r"Scenic Drive in 4K \|")),
    ("POV ...", re.compile(r"^POV\b")),
    ("Highlights from ...", re.compile(r"^Highlights from\b")),
    ("{route} in 4K", re.compile(r"in 4K$")),
]
OTHER_CLASS = "other/legacy"


class ReportError(ValueError):
    """An input the report cannot read; the message is one line for the user."""


def classify_title(title: str) -> str:
    for name, pattern in TITLE_CLASSES:
        if pattern.search(title):
            return name
    return OTHER_CLASS


def load_latest_views(ledger: Path) -> Dict[str, dict]:
    """Latest ``performance_updated`` event per ``data.video_id``."""
    latest: Dict[str, dict] = {}
    try:
        with open(ledger, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReportError(
                        f"event ledger {ledger} line {lineno} is not valid JSON "
                        f"({exc.msg})"
                    ) from exc
                if not isinstance(ev, dict):
                    raise ReportError(
                        f"event ledger {ledger} line {lineno} is not a JSON object"
                    )
                if ev.get("event_type") != "performance_updated":
                    continue
                data = ev.get("data")
                vid = data.get("video_id") if isinstance(data, dict) else None
                if not vid:
                    continue
                ts = ev.get("timestamp")
                if not isinstance(ts, str):
                    raise ReportError(
                        f"event ledger {ledger} line {lineno} has no timestamp"
                    )
                prev = latest.get(vid)
                if prev is None or ts > prev["timestamp"]:
                    latest[vid] = ev
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportError(f"cannot read event ledger {ledger}: {exc}") from exc
    return latest


def load_videos(videos_yaml: Path) -> List[dict]:
    try:
        with open(videos_yaml, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportError(f"cannot read videos.yaml {videos_yaml}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ReportError(
            f"videos.yaml {videos_yaml} is not valid YAML: "
            f"{str(exc).splitlines()[0] if str(exc) else 'parse error'}"
        ) from exc
    if not isinstance(doc, dict) or "videos" not in doc:
        raise ReportError(f"videos.yaml {videos_yaml} has no top-level 'videos' list")
    videos = doc["videos"]
    if videos is None:
        return []
    if not isinstance(videos, list):
        raise ReportError(f"videos.yaml {videos_yaml}: 'videos' is not a list")
    return videos


def _present(video: object, key: str) -> bool:
    return isinstance(video, dict) and video.get(key) not in (None, "")


def join_rows(videos: Iterable[dict], latest: Dict[str, dict]) -> List[dict]:
    """One row per video that has a ledger entry WITH a views measurement: month,
    title class, views, views/day. An entry whose ``views`` is missing or ``None``
    yields no row — a median over measured values cannot absorb an unknown as 0.
    A measured 0 is a measurement and stays a row."""
    rows = []
    for n, v in enumerate(videos, start=1):
        missing = [k for k in ("id", "title", "date") if not _present(v, k)]
        if missing:
            raise ReportError(f"videos.yaml entry {n} is missing {', '.join(missing)}")
        ev = latest.get(v["id"])
        if ev is None:
            continue
        try:
            published = date.fromisoformat(str(v["date"]))
        except ValueError as exc:
            raise ReportError(
                f"videos.yaml entry {n} ({v['id']}): bad date {v['date']!r}; "
                "expected YYYY-MM-DD"
            ) from exc
        try:
            synced = datetime.fromisoformat(ev["timestamp"]).date()
        except ValueError as exc:
            raise ReportError(
                f"event ledger entry for {v['id']}: bad timestamp {ev['timestamp']!r}"
            ) from exc
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


def empty_join_cause(
    videos: List[dict], latest: Dict[str, dict], rows: List[dict]
) -> Optional[str]:
    """Why a report has no rows, or ``None`` when it has some."""
    if rows:
        return None
    if not videos:
        return "no videos in videos.yaml"
    if not latest:
        return "no performance_updated events in the ledger"
    if not any(v["id"] in latest for v in videos):
        return "no video id in common between videos.yaml and the ledger"
    return (
        "the ledger has entries for these videos but no views measurement "
        "(views is missing or null)"
    )


def render_markdown(
    rows: List[dict],
    synced_at: Optional[str] = None,
    unmeasured: int = 0,
    note: Optional[str] = None,
) -> str:
    head = ["# Long-form performance report", ""]
    if synced_at:
        head.append(f"Ledger last sync: {synced_at}  ")
    head.append(f"Videos joined: {len(rows)}  ")
    head.append(f"Videos with a ledger entry but no views measurement: {unmeasured}")
    if note:
        head += ["", f"Note: nothing to report: {note}."]
    body = ["", "## By publish month", "", *_table(rows, "month", "publish month")]
    body += ["", "## By title class", "", *_table(rows, "title_class", "title class")]
    return "\n".join(head + body) + "\n"


def analyze(ledger: Path, videos_yaml: Path) -> Tuple[str, Optional[str]]:
    """The markdown report and, when the join is empty, the one-line cause."""
    latest = load_latest_views(ledger)
    videos = load_videos(videos_yaml)
    rows = join_rows(videos, latest)
    with_entry = sum(1 for v in videos if v["id"] in latest)
    synced = max((e["timestamp"] for e in latest.values()), default=None)
    cause = empty_join_cause(videos, latest, rows)
    text = render_markdown(rows, synced, unmeasured=with_entry - len(rows), note=cause)
    return text, cause


def build_report(ledger: Path, videos_yaml: Path) -> str:
    return analyze(ledger, videos_yaml)[0]
