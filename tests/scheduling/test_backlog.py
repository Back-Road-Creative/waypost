"""``backlog`` — the manifest reader and ready-depth shortfall."""

import pytest

from seo_manager.scheduling import backlog

from .conftest import ANCHOR, at

MANIFEST = """\
- project: routea
  region: south
  sources: ["D:/a"]
  ingest_mode: copy
  lf_mode: regular
- project: routeb
  region: north
  sources: ["D:/b", "D:/c"]
  ingest_mode: link
  lf_mode: regular
  notes: two LFs
- project: routec
  region: south
  sources: ["D:/d"]
  ingest_mode: copy
  lf_mode: regular
"""


def _load(tmp_path, text=MANIFEST):
    p = tmp_path / "drive_backlog.yaml"
    p.write_text(text, encoding="utf-8")
    return backlog.load(p)


def test_rows_in_order(tmp_path):
    rows = _load(tmp_path)
    assert [r.project for r in rows] == ["routea", "routeb", "routec"]
    assert rows[1].sources == ("D:/b", "D:/c") and rows[1].notes == "two LFs"


@pytest.mark.parametrize(
    "bad,match",
    [
        (
            "- project: x\n  region: south\n  sources: []\n  ingest_mode: c\n  lf_mode: r\n",
            "sources",
        ),
        (
            "- project: x\n  region: south\n  sources: [a]\n  ingest_mode: c\n  lf_mode: r\n  bogus: 1\n",
            "bogus",
        ),
        (
            "- region: south\n  sources: [a]\n  ingest_mode: c\n  lf_mode: r\n",
            "project",
        ),
        ("project: x\n", "list"),
    ],
)
def test_malformed_manifest_raises(tmp_path, bad, match):
    with pytest.raises(ValueError, match=match):
        _load(tmp_path, bad)


def test_duplicate_project_raises(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        _load(tmp_path, MANIFEST + MANIFEST.split("- project: routeb")[0])


def test_next_row_skips_rows_already_in_ready_drives(tmp_path, store):
    rows = _load(tmp_path)
    store.register_ready("routea", "south", 1, now=ANCHOR)
    assert backlog.next_row(rows, "south", store).project == "routec"
    store.register_ready("routec", "south", 1, now=ANCHOR)
    assert backlog.next_row(rows, "south", store) is None


def test_failed_rows_are_not_retried_automatically(tmp_path, store):
    rows = _load(tmp_path)
    store.register_ready("routea", "south", 1, now=ANCHOR)
    store.set_status("routea", "failed")
    assert backlog.next_row(rows, "south", store).project == "routec"


def test_shortfall(tmp_path, store, cadence):
    assert backlog.shortfall(store, cadence, "north") == 2
    store.register_ready("a", "north", 1, now=ANCHOR)
    assert backlog.shortfall(store, cadence, "north") == 1
    store.register_ready("b", "north", 1, now=ANCHOR)
    assert backlog.shortfall(store, cadence, "north") == 0


def test_region_case_is_canonical(tmp_path, store):
    rows = _load(tmp_path, MANIFEST.replace("region: south", "region: SOUTH", 1))
    assert rows[0].region == "south"
    assert backlog.next_row(rows, " South", store).project == "routea"


def test_next_row_returns_a_row_still_auditing_but_skips_every_finished_state(
    tmp_path, store
):
    rows = _load(tmp_path)
    store.register_ready("routea", "south", 1, now=ANCHOR, status="auditing")
    assert (
        backlog.next_row(rows, "south", store).project == "routea"
    )  # resume its audit
    store.set_status("routea", "ready")
    assert backlog.next_row(rows, "south", store).project == "routec"
    store.claim_drive("routea", [at("2026-06-14")], "c1")  # claimed is in flight
    assert backlog.next_row(rows, "south", store).project == "routec"
    store.finish_claim("c1", ok=True)  # complete
    assert backlog.next_row(rows, "south", store).project == "routec"
