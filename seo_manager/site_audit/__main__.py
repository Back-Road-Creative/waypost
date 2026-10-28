"""``python -m seo_manager.site_audit``: audit a built site directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from seo_manager.site_audit.audit import AuditReport, audit_build

_RANK = {"info": 0, "warning": 1, "error": 2}
_FAIL_RANK = {"error": 2, "warning": 1, "never": 99}


def _text(report: AuditReport) -> str:
    lines = [
        " ".join(p for p in (f.severity, f.check, f.page, f.target, f.detail) if p)
        for f in report.findings
    ]
    counts = ", ".join(f"{k}={v}" for k, v in report.counts_by_check.items()) or "none"
    lines.append(
        f"pages={report.pages} excluded={report.excluded} "
        f"external_links={report.external_links} findings={len(report.findings)} ({counts})"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m seo_manager.site_audit", description=__doc__
    )
    p.add_argument("--build-dir", required=True, type=Path)
    p.add_argument("--base-url", required=True)
    p.add_argument("--exclude", action="append", default=[], metavar="PREFIX")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--fail-on", choices=sorted(_FAIL_RANK), default="error")
    args = p.parse_args(argv)
    if not args.build_dir.is_dir():
        print(f"error: build dir not found: {args.build_dir}", file=sys.stderr)
        return 2
    try:
        report = audit_build(
            args.build_dir, base_url=args.base_url, exclude=args.exclude
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2) if args.as_json else _text(report))
    limit = _FAIL_RANK[args.fail_on]
    return 1 if any(_RANK[f.severity] >= limit for f in report.findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
