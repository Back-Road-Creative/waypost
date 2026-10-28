"""``seo-manager`` console entry point."""

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from seo_manager import __version__
from seo_manager.analytics.perf_report import ReportError, analyze


def _cmd_report(args: argparse.Namespace) -> int:
    if not args.videos_yaml:
        print(
            "seo-manager report: --videos-yaml is not configured; pass the path "
            "of the videos.yaml to report on",
            file=sys.stderr,
        )
        return 2
    if not args.ledger:
        print(
            "seo-manager report: --ledger is not configured; pass the path "
            "of the event ledger to report on",
            file=sys.stderr,
        )
        return 2
    ledger = Path(args.ledger)
    if not ledger.is_file():
        print(
            f"seo-manager report: no event ledger at {ledger}; "
            "pass --ledger with the path of the ledger to report on",
            file=sys.stderr,
        )
        return 2
    try:
        text, cause = analyze(ledger, Path(args.videos_yaml))
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (ReportError, OSError) as exc:
        print(f"seo-manager report: {exc}", file=sys.stderr)
        return 2
    if cause:
        print(f"seo-manager report: nothing to report: {cause}", file=sys.stderr)
    return 0


_CREATES_DB = frozenset(
    {"register", "import-ledger"}
)  # may start a new database; the rest need --init


def _schedule_context(args):
    from seo_manager.core.profile import load_cadence
    from seo_manager.inputs import default_brand_yaml
    from seo_manager.scheduling.store import ScheduleStore

    # flag > $SEO_BRAND_YAML > brand.yaml in $SEO_INPUTS_DIR
    brand_yaml = args.brand_yaml or default_brand_yaml()
    if not brand_yaml:
        raise ValueError(
            "--brand-yaml (or SEO_BRAND_YAML, or brand.yaml in SEO_INPUTS_DIR) "
            "is required"
        )
    # yaml `dow` is the seed; a decisions db overlays Waypost's decided weekdays
    cadence = load_cadence(brand_yaml, getattr(args, "decisions_db", None))
    if (
        not args.db
    ):  # no code-relative default: two checkouts must not each grow their own database
        raise ValueError("--db (or SEO_SCHEDULE_DB) is required")
    store = ScheduleStore(args.db, cadence.tz, cadence.regions)
    if args.init:
        store.init()
    elif args.action not in _CREATES_DB and not Path(args.db).exists():
        # a typo'd --db must not read as "nothing ready" (a fail-open cron)
        raise ValueError(
            f"schedule database {args.db} does not exist; check --db, or pass --init "
            "to create it"
        )
    return cadence, store


def _when(value, cadence):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=cadence.tz) if dt.tzinfo is None else dt


def _claim_json(c) -> dict:
    return {
        "claim_id": c.claim_id,
        "project": c.project,
        "region": c.region,
        "slots": [s.isoformat() for s in c.slots],
        "lf_count": c.lf_count,
        "output_dir": c.output_dir,
        "state_path": c.state_path,
        "mirror_sha": c.mirror_sha,
        "audit_log": c.audit_log,
        "already_claimed": c.already_claimed,
        "stale": c.stale,
        "lf_mode": c.lf_mode,
        "ingest_mode": c.ingest_mode,
        "confirmed_lfs": list(c.confirmed_lfs),
    }


def _import_ledger(store, path: Path, now) -> dict:
    """One-shot backfill of a content-ledger JSON file's long-forms into ``lf_bookings``."""
    from seo_manager.scheduling.store import SlotTaken

    entries = json.loads(path.read_text(encoding="utf-8")).get("entries", [])
    longforms = sorted(
        (e for e in entries if e.get("content_type") == "longform"),
        key=lambda e: (e["date"], e["time"]),
    )
    from seo_manager.core.profile import canon_region

    # Validate every region first: a bad one must not leave a half-applied import.
    if bad := sorted(
        {
            str(e["region"])
            for e in longforms
            if canon_region(e.get("region")) not in (None, *store.regions)
        }
    ):
        raise ValueError(
            f"ledger has region(s) {bad} the cadence does not know; nothing imported"
        )
    counts: dict = {}
    imported = 0
    skipped: list[dict] = []
    for e in longforms:
        project = (
            e.get("project_name") or f"legacy-{e['date']}-{e.get('region') or 'any'}"
        )
        counts[project] = counts.get(project, 0) + 1
        slot = datetime.strptime(f"{e['date']} {e['time']}", "%Y-%m-%d %H:%M:%S")
        try:
            store.book(
                project,
                counts[project],
                e.get("region"),
                slot,
                video_id=e.get("video_id"),
                booked_at=now,
            )
            imported += 1
        except SlotTaken:
            skipped.append(
                {"project": project, "date": e["date"], "video_id": e.get("video_id")}
            )
    return {
        "imported": imported,
        "skipped_conflicts": len(skipped),
        "skipped": skipped,
        "ignored_non_longform": len(entries) - len(longforms),
    }


def _cmd_schedule(args: argparse.Namespace) -> int:
    """Every slot decision is made here; callers (scheduled jobs) only ask and execute."""
    from seo_manager.scheduling import backlog, report, slots
    from seo_manager.scheduling import claim as claims

    try:
        cadence, store = _schedule_context(args)
        act = args.action
        if act == "report":
            out = report.build(store, cadence, now=_when(args.now, cadence))
        elif act == "next":
            slot = slots.next_free_slot(
                store, cadence, args.region, after=_when(args.after, cadence)
            )
            out = {"region": args.region, "slot": slot.isoformat()}
        elif act == "claim":
            c = claims.claim_or_reason(
                store,
                cadence,
                args.region,
                now=_when(args.now, cadence),
                lead_hours=args.lead_hours,
                expect_sha=args.expect_sha,
                dry_run=args.dry_run,
            )
            if isinstance(c, claims.ClaimMiss):
                out = {"claim": None, "reason": c.reason}
                if c.next_slot is not None:
                    out["next_slot"] = c.next_slot.isoformat()
            else:
                out = _claim_json(c)
        elif act in ("complete", "fail"):
            status = store.finish_claim(args.claim_id, ok=act == "complete")
            out = {"claim_id": args.claim_id, "status": status}
        elif act == "ready-depth":
            depth = store.ready_depth(args.region)
            out = {
                "region": args.region,
                "depth": depth,
                "target": cadence.ready_depth,
                "shortfall": backlog.shortfall(store, cadence, args.region),
            }
        elif act == "set-status":
            store.set_status(args.project, args.status)
            out = {"project": args.project, "status": args.status}
        elif act == "backlog-next":
            cadence.region(args.region)  # unknown region raises
            row = backlog.next_row(backlog.load(args.manifest), args.region, store)
            out = (
                {
                    "project": row.project,
                    "region": row.region,
                    "sources": list(row.sources),
                    "ingest_mode": row.ingest_mode,
                    "lf_mode": row.lf_mode,
                    "notes": row.notes,
                    "day_gpx": row.day_gpx,
                    "camera_utc_offset_hours": row.camera_utc_offset_hours,
                }
                if row
                else {"row": None}
            )
        elif act == "import-ledger":
            out = _import_ledger(
                store, Path(args.ledger_json), _when(args.now, cadence)
            )
        else:  # register
            store.register_ready(
                args.project,
                args.region,
                args.lf_count,
                output_dir=args.output_dir,
                state_path=args.state_path,
                mirror_sha=args.mirror_sha,
                audit_log=args.audit_log,
                status=args.status,
                lf_mode=args.lf_mode,
                ingest_mode=args.ingest_mode,
            )
            out = {"project": args.project, "status": args.status}
    except (
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
        sqlite3.Error,
        yaml.YAMLError,
    ) as exc:
        print(f"seo-manager schedule {args.action}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


def _schedule_options(sp, *, defaults: bool) -> None:
    # Action-level omissions must not overwrite settings supplied before the
    # action. Explicit action-level values take precedence over prefix values.
    sp.add_argument(
        "--brand-yaml",
        default=os.environ.get("SEO_BRAND_YAML") if defaults else argparse.SUPPRESS,
        help="tenant brand.yaml with seo.cadence (env SEO_BRAND_YAML, else brand.yaml in SEO_INPUTS_DIR)",
    )
    sp.add_argument(
        "--db",
        default=os.environ.get("SEO_SCHEDULE_DB") if defaults else argparse.SUPPRESS,
        help="schedule.sqlite3 path (env SEO_SCHEDULE_DB); required, no default",
    )
    sp.add_argument(
        "--decisions-db",
        default=os.environ.get("SEO_DECISIONS_DB") if defaults else argparse.SUPPRESS,
        help="cadence_decisions.sqlite3 (env SEO_DECISIONS_DB); when set, its decided "
        "long-form weekdays replace the brand.yaml dow, which is then only the seed. "
        "No code-relative default, same reason as --db",
    )
    sp.add_argument(
        "--init",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="create --db if it does not exist (register does so on its own)",
    )


def _add_schedule_parser(sub) -> None:
    sp = sub.add_parser("schedule", help="long-form slots: report | next | claim | ...")
    _schedule_options(sp, defaults=True)
    sp.set_defaults(func=_cmd_schedule)
    act = sp.add_subparsers(dest="action", required=True)

    def add(name, help_, *flags):
        p = act.add_parser(name, help=help_)
        _schedule_options(p, defaults=False)
        for f, opts in flags:
            p.add_argument(f, **opts)

    region = ("--region", {"required": True})
    now = ("--now", {"help": "ISO time override (tests, dry runs)"})
    add("report", "booked slots, queue end, next free slots", now)
    add("next", "next free long-form slot for a region", region, ("--after", {}))
    add(
        "claim",
        "reserve the next slot(s) for the oldest ready drive",
        region,
        now,
        ("--lead-hours", {"type": int, "help": "default: cadence lead_hours"}),
        ("--expect-sha", {"help": "refuse a drive registered at another mirror sha"}),
        ("--dry-run", {"action": "store_true", "help": "decide, write nothing"}),
    )
    add("complete", "publish pass succeeded", ("claim_id", {}))
    add("fail", "publish pass failed; free unconfirmed slots", ("claim_id", {}))
    add(
        "set-status",
        "retire or reset a drive (e.g. a stale-sha drive -> failed)",
        ("--project", {"required": True}),
        ("--status", {"required": True}),
    )
    add("ready-depth", "audited drives waiting vs target", region)
    add(
        "register",
        "mark an audited drive ready for a slot",
        ("--project", {"required": True}),
        region,
        ("--lf-count", {"type": int, "required": True}),
        ("--mirror-sha", {"required": True}),
        ("--output-dir", {}),
        ("--state-path", {}),
        ("--audit-log", {}),
        ("--lf-mode", {"help": "how the audit picked LFs: best_moments | full_drive"}),
        ("--ingest-mode", {"help": "how the audit ingested footage: gopro | generic"}),
        ("--status", {"default": "ready", "help": "ready (default) or auditing"}),
    )
    add(
        "backlog-next",
        "next drive_backlog.yaml row to audit for a region (not ready/claimed/complete/failed)",
        ("--manifest", {"required": True}),
        region,
    )
    add(
        "import-ledger",
        "backfill lf_bookings from a content-ledger JSON file",
        ("ledger_json", {}),
        now,
    )


def _age(delta) -> str:
    secs = int(delta.total_seconds())
    if secs < 0:
        return "0s"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def _cmd_collect_status(args: argparse.Namespace) -> int:
    from seo_manager.collection.store import CollectionStore, CollectionStoreError
    from seo_manager.paths import collection_db

    db = Path(args.db) if args.db else collection_db()
    try:
        rows = CollectionStore(db).list_health(args.tenant)
    except CollectionStoreError as exc:
        print(f"seo-manager collect status: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "source": h.source,
                        "last_outcome": h.last_outcome.value
                        if h.last_outcome
                        else None,
                        "last_success_at": h.last_success_at
                        and h.last_success_at.isoformat(),
                        "error_class": h.error_class,
                        "next_eligible": h.next_eligible
                        and h.next_eligible.isoformat(),
                    }
                    for h in rows
                ]
            )
        )
        return 0
    if not rows:
        print(f"no sources have run for tenant {args.tenant}")
        return 0
    for h in rows:
        age = h.age_since_success(now)
        print(
            f"{h.source}  {h.last_outcome.value if h.last_outcome else 'unknown'}  "
            f"success={'never' if age is None else _age(age) + ' ago'}  "
            f"error={h.error_class or 'none'}  "
            f"next_eligible={h.next_eligible.isoformat() if h.next_eligible else 'none'}"
        )
    return 0


def _with_credentials(name: str, body):
    """Run ``body(args, creds)`` with the plain or SOPS token resolved."""
    from seo_manager.collection.credentials import CredentialsError, youtube_credentials

    def handler(args: argparse.Namespace) -> int:
        try:
            with youtube_credentials(
                args.youtube_credentials, args.youtube_credentials_sops
            ) as creds:
                return body(args, creds)
        except CredentialsError as exc:
            print(f"seo-manager collect {name}: {exc}", file=sys.stderr)
            return 2

    return handler


def _cmd_collect_run(args: argparse.Namespace, creds: Path | None) -> int:
    from seo_manager.collection.cycle import run_cycle
    from seo_manager.collection.sources import build_adapters
    from seo_manager.collection.store import CollectionStore, CollectionStoreError
    from seo_manager.paths import collection_db

    def path(value):
        return Path(value) if value else None

    adapters, skipped = build_adapters(
        youtube_credentials=creds,
        video_mapping=path(args.video_mapping),
        search_console_site=args.search_console_site,
        site_build_dir=path(args.site_build_dir),
        site_base_url=args.site_base_url,
        site_exclude=args.site_exclude,
    )
    runs = []
    if adapters:
        store = CollectionStore(Path(args.db) if args.db else collection_db())
        try:
            store.init()
            runs = run_cycle(store, args.tenant, adapters, now=datetime.now(UTC))
        except CollectionStoreError as exc:
            print(f"seo-manager collect run: {exc}", file=sys.stderr)
            return 2
    if args.json:
        rows = [
            {
                "source": r.source,
                "status": r.status,
                "outcome": r.outcome.value if r.outcome else None,
                "added": r.added,
                "error_class": r.error_class,
            }
            for r in runs
        ] + [
            {"source": n.source, "status": "not_run", "reason": n.reason}
            for n in skipped
        ]
        print(json.dumps(rows))
    else:
        for r in runs:
            print(
                f"{r.source}  {r.status}  outcome={r.outcome.value if r.outcome else 'none'}  "
                f"added={r.added}  error={r.error_class or 'none'}"
            )
        for n in skipped:
            print(f"{n.source}  not run: {n.reason}")
    if not adapters:
        print("seo-manager collect run: no source is configured", file=sys.stderr)
        return 2
    return 1 if any(r.status == "failed" for r in runs) else 0


def _youtube_data_client(credentials: Path):
    """The YouTube Data v3 client for read-only video text (tests replace this)."""
    from seo_manager.collection.sources import GoogleClients

    return GoogleClients().analytics(credentials).youtube_data


def _cmd_collect_report(args: argparse.Namespace, creds: Path | None) -> int:
    from seo_manager.collection.search_console import SearchConsoleAdapter
    from seo_manager.collection.site_audit import SiteAuditAdapter
    from seo_manager.collection.store import CollectionStore, CollectionStoreError
    from seo_manager.collection.youtube_reach import YouTubeReachAdapter
    from seo_manager.collection.youtube_search_terms import (
        SOURCE as TERMS_SOURCE,
    )
    from seo_manager.opportunities.report import build_report, render_text
    from seo_manager.opportunities.search_console import (
        find_search_console_opportunities,
    )
    from seo_manager.opportunities.site_technical import (
        find_site_technical_opportunities,
    )
    from seo_manager.opportunities.video_text import fetch_video_text
    from seo_manager.opportunities.youtube_search import (
        find_search_demand_opportunities,
    )
    from seo_manager.paths import collection_db

    if args.limit < 1:
        print("seo-manager collect report: --limit must be >= 1", file=sys.stderr)
        return 2
    db = Path(args.db) if args.db else collection_db()
    store = CollectionStore(db)
    try:
        health = store.list_health(args.tenant)
        observations = store.latest(args.tenant, source=TERMS_SOURCE)
        sc_observations = store.latest(args.tenant, source=SearchConsoleAdapter.source)
        site_observations = store.latest(args.tenant, source=SiteAuditAdapter.source)
    except CollectionStoreError as exc:
        print(f"seo-manager collect report: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    video_ids = sorted({o.subject.split("|", 1)[0] for o in observations})
    texts: dict = {}
    video_text = {"state": "not_configured", "error_class": None}
    if creds and video_ids:
        try:
            client = _youtube_data_client(creds)
        except Exception as exc:  # the type only: a message could echo a token
            video_text = {
                "state": "error",
                "error_class": f"client_build_failed:{type(exc).__name__}",
            }
        else:
            fetched = fetch_video_text(client, video_ids)
            texts = fetched.texts
            video_text = {"state": fetched.state, "error_class": fetched.error_class}
    elif creds:
        video_text = {"state": "not_needed", "error_class": None}
    opportunities = find_search_demand_opportunities(
        observations, texts, tenant=args.tenant, now=now
    ) + find_search_console_opportunities(sc_observations, tenant=args.tenant, now=now)
    opportunities += find_site_technical_opportunities(
        site_observations, tenant=args.tenant, now=now
    )
    report = build_report(
        health,
        (
            TERMS_SOURCE,
            YouTubeReachAdapter.source,
            SearchConsoleAdapter.source,
            SiteAuditAdapter.source,
        ),
        opportunities,
        tenant=args.tenant,
        now=now,
        limit=args.limit,
        video_text=video_text,
    )
    print(json.dumps(report) if args.json else render_text(report), end="")
    if args.json:
        print()
    return 0


def _add_credentials(parser, help_text: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--youtube-credentials", help=help_text)
    group.add_argument(
        "--youtube-credentials-sops",
        help="the same token as a SOPS-encrypted file, decrypted for this run only",
    )


def _add_collect_parser(sub) -> None:
    sp = sub.add_parser("collect", help="collection store: status, run, report")
    act = sp.add_subparsers(dest="action", required=True)
    st = act.add_parser("status", help="per-source health for a tenant")
    st.add_argument("--tenant", required=True)
    st.add_argument(
        "--db", help="default: $SEO_COLLECTION_DB or <data home>/collection.sqlite3"
    )
    st.add_argument("--json", action="store_true", help="machine-readable output")
    st.set_defaults(func=_cmd_collect_status)
    run = act.add_parser("run", help="run one collection cycle for a tenant")
    run.add_argument("--tenant", required=True)
    run.add_argument(
        "--db", help="default: $SEO_COLLECTION_DB or <data home>/collection.sqlite3"
    )
    _add_credentials(run, "authorized-user token JSON file")
    run.add_argument("--video-mapping", help="JSON object: video_id -> content_id")
    run.add_argument("--search-console-site", help="Search Console property URL")
    run.add_argument("--site-build-dir", help="built site directory to audit offline")
    run.add_argument("--site-base-url", help="public base URL of the built site")
    run.add_argument(
        "--site-exclude",
        action="append",
        default=[],
        metavar="PREFIX",
        help="skip pages under this build-relative prefix (repeatable)",
    )
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.set_defaults(func=_with_credentials("run", _cmd_collect_run))
    rep = act.add_parser(
        "report", help="read-only daily report: opportunities and source health"
    )
    rep.add_argument("--tenant", required=True)
    rep.add_argument(
        "--db", help="default: $SEO_COLLECTION_DB or <data home>/collection.sqlite3"
    )
    _add_credentials(
        rep, "authorized-user token JSON file; reads video titles and descriptions"
    )
    rep.add_argument("--limit", type=int, default=10, help="open opportunities shown")
    rep.add_argument("--json", action="store_true", help="machine-readable output")
    rep.set_defaults(func=_with_credentials("report", _cmd_collect_report))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seo-manager")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="LF views by publish month and title class")
    r.add_argument(
        "--ledger",
        help="event ledger holding video-keyed performance_updated events; "
        "required for report, there is no default location",
    )
    r.add_argument(
        "--videos-yaml",
        help="videos.yaml to report on; required, there is no default location",
    )
    r.add_argument("--out", help="write markdown here instead of stdout")
    r.set_defaults(func=_cmd_report)
    _add_schedule_parser(sub)
    _add_collect_parser(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
