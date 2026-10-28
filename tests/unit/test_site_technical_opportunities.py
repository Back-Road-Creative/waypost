"""Rule ``site_technical_defect`` over the offline ``site_audit`` source."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from seo_manager.collection.contracts import MAX_IDENTIFIER_LEN, Observation
from seo_manager.collection.site_audit import SOURCE, SiteAuditAdapter
from seo_manager.opportunities import (
    SiteTechnicalPolicy,
    find_site_technical_opportunities,
)
from seo_manager.opportunities.site_technical import DEFAULT_POLICY, RULE
from seo_manager.site_audit.audit import SEVERITY

BASE = "https://example.com/"
T1 = datetime(2026, 10, 20, 12, tzinfo=UTC)
T2 = T1 + timedelta(days=3)
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def audit(tmp_path: Path, at: datetime, files: dict[str, str]) -> list[Observation]:
    root = tmp_path / f"public-{at.timestamp():.0f}"
    for rel, text in files.items():
        write(root, rel, text)
    obs, _, _ = SiteAuditAdapter(root, BASE).collect("gms", None, at)
    return obs


SITEMAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{BASE}</loc></url></urlset>"
)
ROBOTS = f"User-agent: *\nSitemap: {BASE}sitemap.xml\n"
BROKEN = {
    "index.html": "<html><head></head><body><a href='/gone/'>x</a></body></html>",
    "sitemap.xml": SITEMAP,
    "robots.txt": ROBOTS,
}
CLEAN = {
    "index.html": (
        f"<html><head><link rel='canonical' href='{BASE}'></head><body>ok</body></html>"
    ),
    "sitemap.xml": SITEMAP,
    "robots.txt": ROBOTS,
}


def run(obs, **kw):
    kw.setdefault("tenant", "gms")
    kw.setdefault("now", NOW)
    return find_site_technical_opportunities(obs, **kw)


def site(metric: str, value, at: datetime = T1, tenant: str = "gms") -> Observation:
    return Observation(
        tenant, SOURCE, "site", metric, at, at + timedelta(seconds=1), value, at, None
    )


def finding(
    check: str,
    page: str = "a.html",
    *,
    severity: str | None = None,
    subject: str | None = None,
    prov: str | None = None,
    value: int | None = 1,
    at: datetime = T1,
    target: str | None = None,
) -> Observation:
    sev = severity or SEVERITY.get(check, "error")
    subj = subject or (f"page:{page}" + (f"|{target}" if target else ""))
    return Observation(
        "gms",
        SOURCE,
        subj,
        check,
        at,
        at + timedelta(seconds=1),
        value,
        at,
        prov if prov is not None else f"severity={sev};detail=d",
    )


def audited(*rows: Observation, pages: int = 3, at: datetime = T1) -> list[Observation]:
    return [site("pages", pages, at), *rows]


def test_real_audit_opens_error_and_warning_only(tmp_path):
    obs = audit(tmp_path, T1, BROKEN)
    res = run(obs)
    checks = {o.observed.split(";")[0] for o in res}
    assert len(res) == 2 and all(o.rule == RULE == "site_technical_defect" for o in res)
    by_sev = sorted(o.priority for o in res)
    assert by_sev == [1.0, 2.0]
    top = res[0]
    assert top.asset == "index.html" and top.status == "open"
    assert "broken_internal_link" in top.observed and "/gone/" in top.observed
    assert top.automation == "manual_review_only"
    assert top.policy_version == "site-technical-1"
    assert top.next_evaluation == NOW + timedelta(days=7)
    assert top.missing_evidence == ()
    assert any(e.subject == "site" and e.metric == "findings_broken_internal_link"
               for e in top.evidence)  # fmt: skip
    assert checks  # observed always starts with the check


def test_info_findings_never_open():
    obs = audited(
        finding("not_in_sitemap"),
        finding("canonical_mismatch"),
        finding("noindex"),
    )
    res = run(obs)
    assert [o.asset for o in res] == ["a.html"]
    assert "noindex" in res[0].observed


def test_older_audit_finding_is_fixed_and_persisting_keeps_id():
    old = audited(
        finding("noindex", "gone.html", at=T1),
        finding("canonical_missing", "keep.html", at=T1),
        at=T1,
    )
    new = audited(finding("canonical_missing", "keep.html", at=T2), at=T2)
    only_old = run(old)
    res = run(old + new)
    assert {o.asset for o in only_old} == {"gone.html", "keep.html"}
    assert [o.asset for o in res] == ["keep.html"]
    (kept,) = [o for o in only_old if o.asset == "keep.html"]
    assert res[0].id == kept.id and re.fullmatch(r"opp_[0-9a-f]{16}", kept.id)
    assert res[0].window_start == T2


def test_older_audit_after_clean_newest_gives_nothing():
    old = audited(finding("noindex", at=T1), at=T1)
    assert run([*old, site("pages", 3, T2)]) == []


def test_no_rows_or_other_sources_give_nothing():
    assert run([]) == []
    other = Observation("gms", "search_console", "x", "m", T1, T2, 1, T1, None)
    assert run([other]) == []


def test_zero_missing_or_unknown_pages_gives_nothing():
    row = finding("noindex")
    assert run([site("pages", 0), row]) == []
    assert run([site("pages", None), row]) == []
    assert run([row]) == []


def test_other_tenant_raises_and_naive_now_raises():
    with pytest.raises(ValueError, match="different tenant"):
        run([site("pages", 3, tenant="other")])
    with pytest.raises(ValueError, match="timezone-aware"):
        run(audited(finding("noindex")), now=datetime(2026, 10, 28, 9))


def test_hashed_subject_uses_provenance_raw():
    raw = "page:" + "p" * 150 + "|" + "t" * 150
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    assert len(raw) > MAX_IDENTIFIER_LEN
    obs = finding(
        "broken_internal_link",
        subject=f"page:sha256:{digest}",
        prov=f"severity=error;subject={raw[:200]};detail=d",
    )
    (opp,) = run(audited(obs))
    assert opp.asset == "p" * 150
    assert "t" * 40 in opp.observed
    assert "truncated" in opp.observed  # raw was cut at 200 chars


def test_hashed_subject_without_raw_uses_hash_and_unknown_check_skipped():
    obs = finding("noindex", subject="page:sha256:" + "a" * 32, prov="severity=warning")
    (opp,) = run(audited(obs, finding("invented_check", severity="error")))
    assert opp.asset == "page:sha256:" + "a" * 32


def test_unparseable_severity_skipped():
    assert run(audited(finding("noindex", prov="detail=only"))) == []
    bare = finding("noindex")
    no_prov = Observation(
        bare.tenant, bare.source, bare.subject, bare.metric, bare.window_start,
        bare.window_end, bare.value, bare.reported_at, None,
    )  # fmt: skip
    assert run(audited(no_prov)) == []


def test_cap_per_check_order_and_determinism():
    rows = [finding("noindex", f"p{i:02d}.html") for i in range(30)]
    rows.append(finding("broken_internal_link", "z.html", target="/x/"))
    obs = audited(*rows, site("findings_noindex", 30))
    res = run(obs)
    assert len(res) == 21 == 20 + 1
    assert res[0].priority == 2.0 and res[0].asset == "z.html"
    assert all(o.priority == 1.0 for o in res[1:])
    assert [o.id for o in res[1:]] == sorted(o.id for o in res[1:])
    assert any("30" in r and "findings_noindex" in r for r in res[1].priority_reasons)
    assert run(list(reversed(obs))) == res
    small = run(obs, policy=SiteTechnicalPolicy(max_per_check=2))
    assert len(small) == 3


def test_policy_defaults():
    assert DEFAULT_POLICY.version == "site-technical-1"
    assert DEFAULT_POLICY.severities == ("error", "warning")
    assert DEFAULT_POLICY.max_per_check == 20
    assert DEFAULT_POLICY.reevaluate_after == timedelta(days=7)


def test_proposed_change_by_family_and_fallback():
    rows = [
        finding(c)
        for c in (
            "broken_internal_link",
            "noindex",
            "canonical_missing",
            "sitemap_missing",
            "schema_invalid_json",
            "robots_txt_missing",
        )
    ]
    res = run(audited(*rows))
    assert len(res) == 6
    texts = {o.proposed_change for o in res}
    assert len(texts) == 6
    assert all("source" in t and "rebuild" in t.lower() for t in texts)
    generic = run(audited(finding("canonical_offsite")))
    assert generic[0].proposed_change


BANNED = re.compile(r"rank|traffic|lift|boost|guarantee|%", re.I)


def test_no_ranking_claims_in_any_text_field(tmp_path):
    res = run(audit(tmp_path, T1, BROKEN)) + run(
        audited(finding("noindex"), site("findings_noindex", 1))
    )
    assert res
    for opp in res:
        fields = [
            opp.observed, opp.proposed_change, opp.effort, opp.risk,
            opp.validation, *opp.priority_reasons, *opp.missing_evidence,
        ]  # fmt: skip
        for f in fields:
            assert not BANNED.search(f), f
        assert any("not an impact estimate" in r for r in opp.priority_reasons)
        assert "collect run --site-build-dir" in opp.validation


def test_as_dict_is_json_safe():
    (opp,) = run(audited(finding("noindex")))
    d = json.loads(json.dumps(opp.as_dict()))
    assert d["rule"] == "site_technical_defect"
