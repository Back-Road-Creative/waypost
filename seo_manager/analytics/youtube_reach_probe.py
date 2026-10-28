"""Can the channel's token read a YouTube Reporting API reach report?

``TemplateObservation`` (title_selection.py) needs per-video thumbnail
impressions and click rate split by traffic source. The Analytics API v2 does
not expose them (youtube_sync.py, ANALYTICS_CTR_LIMITATION); the Reporting
API's channel reach reports may. This probe settles it for a real token
instead of a guess, and names the step that stops it:

    no-reach-report-type   the token sees no report type whose id has "reach"
    no-job                 a reach type exists, but no reporting job asks for it
    job-created            --create-job made one per reach type lacking one;
                           YouTube fills each within ~48 h
    no-report-yet          a reach job exists and has produced no report yet
    reach-columns-missing  the newest report lacks the impression columns
    reach-available        it has them (has_traffic_source says if split by source);
                           with several reach jobs, a report carrying the
                           traffic source is preferred over a newer one without
    api-error              the API refused; status and message are printed

``--redacted-sample N`` also prints the first N rows of the chosen report with
every cell outside SAMPLE_VERBATIM replaced by ``h:<sha256[:12]>``, plus the
range of the ctr column: the proof of whether it is a fraction or a percent.

Read-only unless ``--create-job``. The token is the authorized-user JSON,
either decrypted here from the SOPS file or piped in; it is never printed.

    python -m seo_manager.analytics.youtube_reach_probe --sops-file <dir>/youtube_upload_token.json.enc
    <decrypt> | python -m seo_manager.analytics.youtube_reach_probe --token-stdin
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from googleapiclient.errors import HttpError

REACH_METRICS = ("video_thumbnail_impressions", "video_thumbnail_impressions_ctr")
TRAFFIC_SOURCE_COLUMN = "traffic_source_type"
JOB_NAME = "waypost-reach-probe"
IMPRESSIONS_COLUMN = REACH_METRICS[0]
CTR_COLUMN = REACH_METRICS[1]
# Cells printed verbatim in a redacted sample; every other column is hashed.
SAMPLE_VERBATIM = (
    "date",
    "traffic_source_type",
    "operating_system",
    "device_type",
    IMPRESSIONS_COLUMN,
    CTR_COLUMN,
)
MAX_SAMPLE_ROWS = 50


def _pages(request: Callable[..., Any], key: str, **kwargs: Any) -> list[dict]:
    items: list[dict] = []
    token = None
    while True:
        page = request(pageToken=token, **kwargs).execute()
        items.extend(page.get(key, []))
        token = page.get("nextPageToken")
        if not token:
            return items


def _redact(column: str, cell: str) -> str:
    if column in SAMPLE_VERBATIM or not cell:
        return cell
    return "h:" + hashlib.sha256(cell.encode()).hexdigest()[:12]


def _summarise(body: list[list[str]], columns: list[str], column: str) -> dict:
    """Count, range and non-numeric tally of one column over every body row."""
    i = columns.index(column)
    values: list[float] = []
    non_numeric = 0
    for row in body:
        try:
            values.append(float(row[i]))
        except (IndexError, ValueError):
            non_numeric += 1
    return {
        "count": len(values),
        "min": min(values, default=None),
        "max": max(values, default=None),
        "over_1": sum(v > 1 for v in values),
        "non_numeric": non_numeric,
    }


def probe(
    reporting: Any,
    download: Callable[[str], str],
    *,
    create_job: bool = False,
    sample_rows: int = 0,
) -> dict:
    """Walk report types -> jobs -> newest report -> its columns."""
    types = _pages(
        reporting.reportTypes().list, "reportTypes", includeSystemManaged=True
    )
    reach_types = sorted(t["id"] for t in types if "reach" in t["id"])
    result: dict[str, Any] = {"reach_types": reach_types}
    if not reach_types:
        result["verdict"] = "no-reach-report-type"
        return result

    jobs = [
        j
        for j in _pages(reporting.jobs().list, "jobs", includeSystemManaged=True)
        if j.get("reportTypeId") in reach_types
    ]
    result["jobs"] = [j["id"] for j in jobs]
    # Every reach type gets a job: they differ in dimensions, and only the
    # report's columns say which one carries the traffic source.
    without_job = [t for t in reach_types if t not in {j["reportTypeId"] for j in jobs}]
    if create_job and without_job:
        result["created_jobs"] = [
            reporting.jobs()
            .create(body={"reportTypeId": t, "name": JOB_NAME})
            .execute()
            .get("id")
            for t in without_job
        ]
        result["verdict"] = "job-created"
        return result
    if not jobs:
        result["verdict"] = "no-job"
        return result

    reports_by_job = {
        j["id"]: _pages(reporting.jobs().reports().list, "reports", jobId=j["id"])
        for j in jobs
    }
    result["reports"] = sum(len(r) for r in reports_by_job.values())
    if not result["reports"]:
        result["verdict"] = "no-report-yet"
        return result

    # The newest report of each job; one with the traffic source wins over a
    # newer one without it, then one with the reach metrics, then recency.
    candidates = []
    for reports in reports_by_job.values():
        if not reports:
            continue
        newest = max(reports, key=lambda r: r.get("createTime", ""))
        rows = list(csv.reader(io.StringIO(download(newest["downloadUrl"]))))
        columns = rows[0] if rows else []
        candidates.append((newest, columns, rows[1:]))
    newest, columns, body = max(
        candidates,
        key=lambda c: (
            TRAFFIC_SOURCE_COLUMN in c[1],
            all(m in c[1] for m in REACH_METRICS),
            c[0].get("createTime", ""),
        ),
    )
    result.update(
        newest_report=newest["id"],
        newest_window=f"{newest.get('startTime', '?')}..{newest.get('endTime', '?')}",
        columns=columns,
        rows=len(body),
        has_reach_metrics=all(c in columns for c in REACH_METRICS),
        has_traffic_source=TRAFFIC_SOURCE_COLUMN in columns,
    )
    if "video_id" in columns:
        i = columns.index("video_id")
        result["videos"] = len({r[i] for r in body if len(r) > i})
    result["verdict"] = (
        "reach-available" if result["has_reach_metrics"] else "reach-columns-missing"
    )
    if result["verdict"] == "reach-available" and sample_rows > 0:
        result["sample_header"] = columns
        result["sample"] = [
            [_redact(columns[i], cell) for i, cell in enumerate(row[: len(columns)])]
            for row in body[:sample_rows]
        ]
        result["ctr_summary"] = _summarise(body, columns, CTR_COLUMN)
        impressions = _summarise(body, columns, IMPRESSIONS_COLUMN)
        result["impressions_summary"] = {
            k: impressions[k] for k in ("count", "min", "max")
        }
    return result


def format_lines(result: dict) -> list[str]:
    """``REACH_PROBE verdict=...`` first, then one line per known field."""
    lines = [f"REACH_PROBE verdict={result['verdict']}"]
    for key, value in result.items():
        if key == "verdict":
            continue
        if key == "sample":
            lines.extend(
                f"REACH_PROBE sample[{n}]={','.join(row)}"
                for n, row in enumerate(value)
            )
            continue
        if isinstance(value, dict):
            value = ",".join(
                f"{k}={'-' if v is None else format(v, 'g')}" for k, v in value.items()
            )
        if isinstance(value, list):
            value = ",".join(str(v) for v in value) or "-"
        lines.append(f"REACH_PROBE {key}={value}")
    return lines


def _build(info: dict) -> tuple[Any, Callable[[str], str]]:
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(info, info.get("scopes"))
    reporting = build(
        "youtubereporting", "v1", credentials=creds, cache_discovery=False
    )
    session = AuthorizedSession(creds)

    def download(url: str) -> str:
        resp = session.get(url, timeout=120)
        resp.raise_for_status()
        return resp.text

    return reporting, download


def _sops_token(path: str) -> tuple[dict | None, str]:
    """Decrypt the token file; a sops binary-format ``{"data": "<json>"}`` is unwrapped."""
    proc = subprocess.run(
        ["sops", "-d", "--output-type", "json", path],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None, f"REACH_PROBE verdict=sops-error rc={proc.returncode}"
    info = json.loads(proc.stdout)
    if sorted(info) == ["data"] and isinstance(info["data"], str):
        info = json.loads(info["data"])
    return info, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the authorized-user token JSON from stdin",
    )
    parser.add_argument("--sops-file", help="decrypt the token from this SOPS file")
    parser.add_argument(
        "--create-job",
        action="store_true",
        help="create a reporting job for each reach type that has none",
    )
    parser.add_argument(
        "--redacted-sample",
        type=int,
        default=0,
        metavar="N",
        help=f"print N (0..{MAX_SAMPLE_ROWS}) hashed rows of the chosen report and "
        "the ctr range; read-only, not with --create-job",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.redacted_sample <= MAX_SAMPLE_ROWS or (
        args.redacted_sample and args.create_job
    ):
        print(
            f"REACH_PROBE verdict=usage: --redacted-sample takes 0..{MAX_SAMPLE_ROWS} "
            "and cannot be combined with --create-job",
            file=sys.stderr,
        )
        return 64
    if args.sops_file:
        info, error = _sops_token(args.sops_file)
        if info is None:
            print(error)
            return 3
    elif args.token_stdin:
        info = json.load(sys.stdin)
    else:
        print(
            "REACH_PROBE verdict=usage: pass --sops-file <token.enc> or --token-stdin",
            file=sys.stderr,
        )
        return 64
    reporting, download = _build(info)
    try:
        result = probe(
            reporting,
            download,
            create_job=args.create_job,
            sample_rows=args.redacted_sample,
        )
    except HttpError as e:
        status = getattr(e.resp, "status", "?")
        print(f"REACH_PROBE verdict=api-error status={status}")
        print(f"REACH_PROBE message={e._get_reason()}")
        return 2
    print("\n".join(format_lines(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
