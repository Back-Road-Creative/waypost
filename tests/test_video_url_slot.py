"""``video_url`` slot: a Short may link its parent drive, only by evidence.

The slot value is an evidence id whose item is of kind ``parent_video`` and
whose value is an 11-character YouTube id; the review renders the link itself,
so a free URL can never reach a description. Every id here is invented.
"""

import hashlib

import drive_text_synthetic as syn
import pytest
from drive_text_synthetic import golden_entries

from seo_manager.analytics.drive_templates import (
    LAYOUT,
    PARAMS,
    drive_hashes,
)
from seo_manager.analytics.final_text_params import Slot
from seo_manager.analytics.final_text_review import (
    AGREE,
    DEFAULT_TEXT_POLICY,
    PARENT_VIDEO,
    EvidenceItem,
    SurfaceReview,
    _check_backing,
    _Reasons,
)
from seo_manager.analytics.final_text_surfaces import (
    ParamBlock,
    TemplateBlock,
    build_visible,
)

TID = "description.short.parent"
VID = "aB3_dE-fG1h"
ITEM = {"evidence_id": "v-1", "kind": PARENT_VIDEO, "value": VID, "source": "upload"}


def _entry(label="Full drive", url="v-1", text=None):
    entry = build_visible(
        [ParamBlock("description.short", TID, {"label": "Full drive", "url": "v-1"},
                    {"v-1": VID})]
    )[0]  # fmt: skip
    entry["slots"] = {"label": label, "url": url}
    if text is not None:
        entry["text"] = text
    return entry


def _review(entries, layout=None, extra=(ITEM,), fmt="short"):
    """The synthetic review under a ``fmt`` (``short``/``longform``) decision."""
    original = syn._decision
    syn._decision = lambda: {**original(), "format": fmt}
    try:
        return syn._review(entries, layout=layout, extra=extra)
    finally:
        syn._decision = original


def _short(entry=None, extra=(ITEM,), layout=None):
    return _review([entry or _entry()], layout=layout, extra=extra)


def test_a_parent_video_link_agrees_and_renders_a_youtu_be_url():
    entry = _entry()
    assert entry["text"] == f"Full drive: https://youtu.be/{VID}"
    assert entry["evidence_ids"] == ["v-1"]
    assert _short(entry).verdict == AGREE


@pytest.mark.parametrize("url", [f"https://youtu.be/{VID}", VID, "", 7, None])
def test_a_raw_url_in_the_slot_is_refused(url):
    entry = _entry(url=url, text=f"Full drive: https://youtu.be/{VID}")
    assert "param_video:description.short:url" in _short(entry).reasons


def test_a_place_kind_in_a_video_url_slot_is_refused():
    place = {"evidence_id": "v-1", "kind": "location", "value": VID, "source": "gps"}
    assert "param_video:description.short:url" in _short(extra=(place,)).reasons


@pytest.mark.parametrize(
    "bad", ["short", "toolongvideoid1", "has space!!", "https://x.y"]
)
def test_a_malformed_video_id_is_refused(bad):
    item = {**ITEM, "value": bad}
    entry = _entry(text=f"Full drive: https://youtu.be/{bad}")
    assert (
        "param_video_id:description.short:url" in _short(entry, extra=(item,)).reasons
    )


def test_missing_evidence_is_refused():
    assert "param_video:description.short:url" in _short(extra=()).reasons


def test_ambiguous_evidence_is_refused():
    other = {**ITEM, "value": "zzzzzzzzzzz"}
    reasons = _short(extra=(ITEM, other)).reasons
    assert "evidence_ambiguous:v-1" in reasons
    assert "param_video:description.short:url" in reasons


def test_uncited_or_extra_evidence_ids_are_refused():
    entry = _entry()
    entry["evidence_ids"] = []
    assert "param_video:description.short:url" in _short(entry).reasons
    entry["evidence_ids"] = ["v-1", "p-1"]
    assert "param_evidence:description.short" in _short(entry).reasons


def test_a_hash_of_the_slot_values_cannot_swap_the_cited_video():
    # The cited id is the slot value; a different item cannot be smuggled in.
    other = {**ITEM, "evidence_id": "v-2", "value": "zzzzzzzzzzz"}
    entry = _entry()
    entry["evidence_ids"] = ["v-2"]
    assert (
        "param_video:description.short:url"
        in _short(entry, extra=(ITEM, other)).reasons
    )


def test_the_text_must_be_the_rendered_link():
    entry = _entry(text="Full drive: https://youtu.be/zzzzzzzzzzz")
    assert "param_text:description.short" in _short(entry).reasons


def test_parent_video_evidence_is_not_a_place():
    # Were the video item passed to the name backing, the words around its id
    # (https, youtu, be) would count as unbacked names: the check discriminates, and the review never sends it.
    item = EvidenceItem(**ITEM)
    surface = SurfaceReview(
        "description.short", f"https://youtu.be/{item.value}", (item,)
    )
    probe = _Reasons()
    _check_backing(surface, [], "place_not_on_route", DEFAULT_TEXT_POLICY, probe)
    assert any(r.startswith("name_unbacked") for r in probe.items)
    review = _short()
    assert review.verdict == AGREE
    assert not [r for r in review.reasons if r.startswith("name_unbacked")]


def test_the_stored_surface_keeps_the_video_evidence():
    stored = next(x for x in _short().surfaces if x.template_id == TID)
    assert [i.evidence_id for i in stored.evidence] == ["v-1"]
    assert stored.evidence[0].kind == PARENT_VIDEO


def test_the_link_is_refused_outside_a_short_even_with_no_layout():
    review = _review([_entry()], fmt="longform")
    assert "param_parent_not_short:description.short" in review.reasons


def test_the_link_is_refused_in_a_long_form_layout_decision_too():
    entries = [*golden_entries(), _entry()]
    reasons = _review(entries, layout=LAYOUT, fmt="longform").reasons
    assert "param_parent_not_short:description.short" in reasons


def test_a_second_link_is_refused():
    second = _entry()
    second["surface"] = "description.short2"
    reasons = _review([_entry(), second]).reasons
    assert "param_parent_repeat:description.short2" in reasons


def test_the_link_must_be_the_last_description_block():
    first = _entry()
    cta = build_visible([TemplateBlock("description.cta", "description.cta")])[0]
    assert "param_parent_not_last:description.short" in _review([first, cta]).reasons
    assert (
        "param_parent_not_last:description.short" not in _review([cta, first]).reasons
    )


@pytest.mark.parametrize("surface", ["ost", "foo", "chapter.1"])
def test_the_link_on_a_non_description_surface_is_refused(surface):
    entry = _entry()
    entry["surface"] = surface
    reasons = _short(entry, layout=LAYOUT).reasons
    assert f"param_parent_surface:{surface}" in reasons


@pytest.mark.parametrize(
    "bad", ["abcdefghij\u00e9", "abcdefghij\n", "\uff21bcdefghijk"]
)
def test_a_non_ascii_or_newline_terminated_id_is_refused(bad):
    item = {**ITEM, "value": bad}
    entry = _entry(text=f"Full drive: https://youtu.be/{bad}")
    assert (
        "param_video_id:description.short:url" in _short(entry, extra=(item,)).reasons
    )


@pytest.mark.parametrize("label", ["Full Drive", "Watch now", "", "full drive"])
def test_an_unknown_label_is_refused(label):
    entry = _entry(label=label, text=f"{label}: https://youtu.be/{VID}")
    assert "param_slot:description.short:label" in _short(entry).reasons


def test_the_highlights_label_agrees():
    entry = build_visible(
        [ParamBlock("description.short", TID,
                    {"label": "Highlights reel", "url": "v-1"}, {"v-1": VID})]
    )[0]  # fmt: skip
    assert _short(entry).verdict == AGREE


def test_a_long_form_layout_still_refuses_the_short_block():
    entries = [*golden_entries(), _entry()]
    reasons = _review(entries, layout=LAYOUT, extra=(ITEM,)).reasons
    assert "layout_free_line:description.short" in reasons


def test_video_url_is_a_registered_slot_type_and_the_template_is_registered():
    template = PARAMS[TID]
    assert template.skeleton == "{label}: {url}"
    assert template.slots == (
        Slot("label", "enum", values=("Full drive", "Highlights reel")),
        Slot("url", "video_url"),
    )
    assert TID in dict(drive_hashes())


# Pinned before the slot type existed: adding one moved no existing hash.
def test_existing_template_hashes_did_not_move():
    rest = sorted((i, h) for i, h in drive_hashes() if i != TID)
    digest = hashlib.sha256(repr(rest).encode()).hexdigest()
    assert digest == EXISTING_DIGEST


EXISTING_DIGEST = "ece318dbeaf58c74ae6fbe78984d8e94bcdafd7b1f32c81d03ff7044e3b96e81"
