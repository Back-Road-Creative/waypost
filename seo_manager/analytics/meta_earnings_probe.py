"""Can the stored Facebook Page token read the Page's earnings insights?

Before any monetization comparison is built on Meta earnings, this probe asks
the Graph API ``/{page-id}/insights`` edge for each Page-level earnings metric
and reports, per metric, whether it came back:

    status=ok      points=N latest=<value of the newest point>
    status=empty   the call succeeded but returned no data points
    status=error   the Graph API refused; its code and message are printed

The verdict is ``available`` when at least one metric is ``ok``, otherwise
``unavailable``. Meta's reference lists ``read_insights`` and
``pages_read_engagement`` (plus the ANALYZE task on the Page) for this edge,
so a code 10 or 200 refusal names a missing permission.

Read-only. The credentials are the ``meta_publishing.json`` fields
(``page_id``, ``page_access_token``, optional ``graph_version``), either
decrypted here from the SOPS file or piped in; the token is never printed.

    python -m seo_manager.analytics.meta_earnings_probe --sops-file <dir>/meta_publishing.json.enc
    <decrypt> | python -m seo_manager.analytics.meta_earnings_probe --creds-stdin
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

DEFAULT_GRAPH_VERSION = "v21.0"

# Page-level earnings metrics from Meta's Insights reference, read 2026-10-26.
EARNINGS_METRICS: tuple[str, ...] = (
    "monetization_approximate_earnings",
    "content_monetization_earnings",
    "creator_monetization_qualified_views",
    "page_daily_video_ad_break_earnings_by_crosspost_status",
)

REQUIRED_FIELDS = ("page_id", "page_access_token")

Get = Callable[[str, dict], tuple[int, dict]]


def probe(creds: dict, get: Get) -> dict:
    """Ask for each earnings metric; ``get(url, params)`` returns (status, body)."""
    missing = [f for f in REQUIRED_FIELDS if not creds.get(f)]
    if missing:
        return {"verdict": "missing-credentials", "missing": missing}
    token = creds["page_access_token"]
    version = creds.get("graph_version") or DEFAULT_GRAPH_VERSION
    url = f"https://graph.facebook.com/{version}/{creds['page_id']}/insights"
    metrics: dict[str, dict] = {}
    for metric in EARNINGS_METRICS:
        status, body = get(
            url, {"metric": metric, "period": "day", "access_token": token}
        )
        error = body.get("error")
        if status != 200 or error:
            error = error or {}
            message = str(error.get("message", f"HTTP {status}")).replace(
                token, "<redacted>"
            )
            metrics[metric] = {
                "status": "error",
                "code": error.get("code", status),
                "message": message,
            }
            continue
        values = [
            v for series in body.get("data", []) for v in series.get("values", [])
        ]
        if not values:
            metrics[metric] = {"status": "empty", "points": 0}
            continue
        metrics[metric] = {
            "status": "ok",
            "points": len(values),
            "latest": values[-1].get("value"),
        }
    ok = any(m["status"] == "ok" for m in metrics.values())
    return {"verdict": "available" if ok else "unavailable", "metrics": metrics}


def format_lines(result: dict) -> list[str]:
    """``META_EARNINGS verdict=...`` first, then one line per metric."""
    lines = [f"META_EARNINGS verdict={result['verdict']}"]
    if "missing" in result:
        lines.append(f"META_EARNINGS missing={','.join(result['missing'])}")
    for metric, m in result.get("metrics", {}).items():
        if m["status"] == "ok":
            detail = (
                f"points={m['points']} latest={json.dumps(m['latest'], sort_keys=True)}"
            )
        elif m["status"] == "empty":
            detail = "points=0"
        else:
            detail = f"code={m['code']} message={m['message']}"
        lines.append(f"META_EARNINGS metric={metric} status={m['status']} {detail}")
    return lines


def _http_get(url: str, params: dict) -> tuple[int, dict]:
    import requests

    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        # The exception text can carry the request URL, token included; probe() redacts it.
        return 0, {"error": {"code": "network", "message": str(e)}}
    try:
        body: Any = resp.json()
    except ValueError:
        body = {"error": {"code": resp.status_code, "message": "non-JSON response"}}
    return resp.status_code, body if isinstance(body, dict) else {}


def _sops_creds(path: str) -> tuple[dict | None, str]:
    """Decrypt the credentials file; a sops binary-format ``{"data": "<json>"}`` is unwrapped."""
    proc = subprocess.run(
        ["sops", "-d", "--output-type", "json", path],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None, f"META_EARNINGS verdict=sops-error rc={proc.returncode}"
    creds = json.loads(proc.stdout)
    if sorted(creds) == ["data"] and isinstance(creds["data"], str):
        creds = json.loads(creds["data"])
    return creds, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--creds-stdin",
        action="store_true",
        help="read the meta_publishing credentials JSON from stdin",
    )
    parser.add_argument(
        "--sops-file", help="decrypt the credentials from this SOPS file"
    )
    args = parser.parse_args(argv)
    if args.sops_file:
        creds, error = _sops_creds(args.sops_file)
        if creds is None:
            print(error)
            return 3
    elif args.creds_stdin:
        creds = json.load(sys.stdin)
    else:
        print(
            "META_EARNINGS verdict=usage: pass --sops-file <meta_publishing.json.enc> or --creds-stdin",
            file=sys.stderr,
        )
        return 64
    result = probe(creds, _http_get)
    print("\n".join(format_lines(result)))
    return 3 if result["verdict"] == "missing-credentials" else 0


if __name__ == "__main__":
    raise SystemExit(main())
