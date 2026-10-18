"""Typed surfaces: a description, its chapters and its tags are reviewed too.

Every place here is invented. A description is never reviewed as free prose:
Waypost emits it as typed surfaces. A place line is checked like on-screen
text. A template block is Waypost-owned boilerplate, accepted only by its id
and the hash of its exact bytes. A chapter is a segment surface over its own
window. A tag is either backed by evidence or taken from a registered tag
template. Any other kind is refused.
"""

import json

import pytest

from seo_manager.analytics.final_text_review import (
    AGREE,
    DEFAULT_TEXT_POLICY,
    RECORD_VERSION,
    REVIEW_VERSION,
    FinalTextReviewRefused,
    _fingerprint_of,
    review_final_text,
    review_to_dict,
    verify_review,
)
from seo_manager.analytics.final_text_surfaces import (
    CHAPTER,
    PLACE_LINE,
    TAG,
    TEMPLATE,
    TEMPLATES,
    VISIBLE_KINDS,
    Chapter,
    PlaceLine,
    Tag,
    TemplateBlock,
    build_visible,
    template_hash,
)
from seo_manager.analytics.title_selection import (
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TitleFacts,
    record_to_dict,
    select_title,
)

ROUTE = "Brindlemoor to Quillhaven, Harrow County"
TITLE = "Brindlemoor to Quillhaven, Harrow County in 4K"
SOURCE_FP = "src-" + "a" * 60
PLAN_FP = "plan-" + "b" * 59
DATE = "2026-03-02"
FOOTER = "description.footer"
HEADING = "description.chapters_heading"
SCENIC = "tag.scenic_drive"

EVIDENCE = [
    {"evidence_id": "p-1", "kind": "location", "value": "Brindlemoor", "source": "gps"},
    {"evidence_id": "p-2", "kind": "location", "value": "Quillhaven", "source": "gps"},
    {"evidence_id": "r-1", "kind": "region", "value": "Harrow County", "source": "geo"},
    {"evidence_id": "d-1", "kind": "date", "value": DATE, "source": "file_metadata"},
]


def _decision():
    facts = TitleFacts(
        format="longform",
        baseline_title=TITLE,
        origin=Place("Brindlemoor", "p-1"),
        destination=Place("Quillhaven", "p-2"),
        region=Place("Harrow County", "r-1"),
        suffix=" in 4K",
    )
    snap = EvidenceSnapshot(as_of="2026-03-20T00:00:00")
    return record_to_dict(select_title(facts, snap, DEFAULT_POLICY))


def _line(surface, text, ids=("p-1", "p-2", "r-1")):
    return {
        "surface": surface,
        "kind": PLACE_LINE,
        "text": text,
        "evidence_ids": list(ids),
        "scope": "whole",
    }


def _template(surface, template_id, text=None):
    return {
        "surface": surface,
        "kind": TEMPLATE,
        "text": TEMPLATES[template_id] if text is None else text,
        "template_id": template_id,
    }


def _chapter(surface, text, ids, window):
    return {
        "surface": surface,
        "kind": CHAPTER,
        "text": text,
        "evidence_ids": list(ids),
        "segment_places": list(window),
    }


def _review(*extra, ost=ROUTE):
    record = {
        "record_version": RECORD_VERSION,
        "approval_hash": "c" * 64,
        "title": TITLE,
        "recording_date": DATE,
        "date_evidence_id": "d-1",
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
        "route_places": [
            {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
            {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
        ],
        "region_claims": [],
        "visible": [_line("ost", ost), *extra],
    }
    return review_final_text(record, _decision(), EVIDENCE)


def _visible(review):
    return {
        s.surface: s.text
        for s in review.surfaces
        if s.surface not in ("title", "recording_date")
    }


def _verify(review, visible=None):
    verify_review(
        review,
        final_title=TITLE,
        visible=_visible(review) if visible is None else visible,
        source_fingerprint=SOURCE_FP,
        plan_fingerprint=PLAN_FP,
        policy_version=DEFAULT_POLICY.version,
    )


# --- W1: a place line is checked like on-screen text ------------------------


def test_w1_a_backed_place_line_agrees():
    review = _review(_line("description.route", ROUTE))
    assert review.verdict == AGREE, review.reasons
    _verify(review)


def test_w1_an_unbacked_name_on_a_place_line_is_refused():
    review = _review(_line("description.route", f"{ROUTE} past Larkspur"))
    assert "name_unbacked:description.route:larkspur" in review.reasons
    assert "name_unbacked:description.route:past" in review.reasons


# --- W2: a template block passes on its id and the hash of its bytes ---------


def test_w2_a_registered_template_block_is_accepted():
    review = _review(_template("description.footer", FOOTER))
    assert review.verdict == AGREE, review.reasons
    assert dict(review.text_policy["templates"])[FOOTER] == template_hash(
        TEMPLATES[FOOTER]
    )
    _verify(review)


def test_w2_one_changed_byte_is_refused():
    text = TEMPLATES[FOOTER][:-1] + "!"
    review = _review(_template("description.footer", FOOTER, text))
    assert "template_changed:description.footer:description.footer" in review.reasons


def test_w2_an_unknown_template_id_is_refused():
    review = _review(_template("description.footer", FOOTER) | {"template_id": "x.y"})
    assert "template_unknown:description.footer:x.y" in review.reasons


def test_w2_a_known_id_carrying_another_templates_bytes_is_refused():
    review = _review(_template("description.footer", FOOTER, TEMPLATES[HEADING]))
    assert "template_changed:description.footer:description.footer" in review.reasons


def test_w2_a_template_block_citing_evidence_is_refused():
    block = _template("description.footer", FOOTER) | {"evidence_ids": ["p-1"]}
    assert "template_with_evidence:description.footer" in _review(block).reasons


# --- W3: there is no free-prose kind ----------------------------------------


def test_w3_the_kinds_are_closed():
    assert (
        frozenset({PLACE_LINE, TEMPLATE, CHAPTER, TAG, "param_template"})
        == VISIBLE_KINDS
    )


@pytest.mark.parametrize("kind", ["prose", "description", "title", None])
def test_w3_a_free_text_surface_is_refused(kind):
    entry = _line("description", "A calm drive through farm country")
    if kind is None:
        del entry["kind"]
    else:
        entry["kind"] = kind
    reasons = _review(entry).reasons
    assert "surface_kind:description" in reasons


# --- W4: an invented place is refused however it is written -----------------


@pytest.mark.parametrize(
    "text, word",
    [
        ("Brindlemoor to Quillhaven via larkspur", "larkspur"),
        ("Larkspur to Quillhaven via Brindlemoor", "larkspur"),
        ("brindlemoor to quillhaven via LARKSPUR", "larkspur"),
    ],
)
def test_w4_an_invented_place_on_a_place_line_is_refused(text, word):
    review = _review(_line("description.route", text, ("p-1", "p-2")))
    assert f"name_unbacked:description.route:{word}" in review.reasons


# --- W5: a chapter is a segment surface over its own window -----------------


def _chapters(second):
    return (
        _chapter("chapter.1", "0:00 Brindlemoor", ["p-1"], ["p-1"]),
        second,
    )


def test_w5_chapters_naming_their_own_windows_agree():
    second = _chapter("chapter.2", "20:00 Quillhaven", ["p-2"], ["p-2"])
    review = _review(*_chapters(second))
    assert review.verdict == AGREE, review.reasons
    _verify(review)


def test_w5_a_place_backed_only_by_another_chapters_window_is_refused():
    second = _chapter("chapter.2", "20:00 Brindlemoor", ["p-1"], ["p-2"])
    reasons = _review(*_chapters(second)).reasons
    assert "place_not_in_segment:chapter.2:p-1" in reasons
    assert "segment_place_dropped:chapter.2:p-2" in reasons


@pytest.mark.parametrize("window", [None, []])
def test_w5_a_chapter_with_no_window_is_refused(window):
    chapter = _chapter("chapter.1", "0:00 Brindlemoor", ["p-1"], ["p-1"])
    if window is None:
        del chapter["segment_places"]
    else:
        chapter["segment_places"] = window
    assert "chapter_without_window:chapter.1" in _review(chapter).reasons


# --- W6: a tag is backed by evidence or comes from a tag template ------------


def _tag(surface, text, ids=(), template_id=None):
    tag = {"surface": surface, "kind": TAG, "text": text, "evidence_ids": list(ids)}
    if template_id is not None:
        tag["template_id"] = template_id
    return tag


def test_w6_a_backed_place_tag_agrees():
    review = _review(_tag("tag.1", "Quillhaven", ["p-2"]))
    assert review.verdict == AGREE, review.reasons


def test_w6_a_tag_from_the_registered_tag_template_agrees():
    review = _review(_tag("tag.1", "scenic drive", template_id=SCENIC))
    assert TEMPLATES[SCENIC] == "scenic drive"
    assert review.verdict == AGREE, review.reasons
    _verify(review)


def test_w6_the_same_words_unregistered_are_refused():
    reasons = _review(_tag("tag.1", "scenic drive")).reasons
    assert "surface_without_evidence:tag.1" in reasons
    assert "name_unbacked:tag.1:scenic" in reasons


def test_w6_a_tag_naming_an_unbacked_place_is_refused():
    reasons = _review(_tag("tag.1", "Larkspur Quillhaven", ["p-2"])).reasons
    assert "name_unbacked:tag.1:larkspur" in reasons


def test_w6_a_template_tag_with_changed_words_is_refused():
    reasons = _review(_tag("tag.1", "scenic Larkspur", template_id=SCENIC)).reasons
    assert "template_changed:tag.1:tag.scenic_drive" in reasons


# --- Review version: old reviews are refused; the current one round-trips ---


def test_w7_the_review_is_version_6():
    assert REVIEW_VERSION == "final-text-review-6"
    assert RECORD_VERSION == "final-text-record-2"


def test_w7_a_review_3_record_is_refused_at_upload():
    review = _review()
    body = review_to_dict(review)
    body["review_version"] = "final-text-review-3"
    body["fingerprint"] = _fingerprint_of(body)
    with pytest.raises(FinalTextReviewRefused) as caught:
        _verify(body, _visible(review))
    assert caught.value.code == "stale:review_version"


def test_w7_a_v4_review_round_trips_through_json():
    review = _review(
        _template("description.footer", FOOTER),
        _chapter("chapter.1", "0:00 Brindlemoor", ["p-1"], ["p-1"]),
        _tag("tag.1", "scenic drive", template_id=SCENIC),
    )
    assert review.verdict == AGREE, review.reasons
    body = json.loads(json.dumps(review_to_dict(review)))
    visible = {
        s["surface"]: s["text"]
        for s in body["surfaces"]
        if s["surface"] not in ("title", "recording_date")
    }
    verify_review(
        body,
        final_title=TITLE,
        visible=visible,
        source_fingerprint=SOURCE_FP,
        plan_fingerprint=PLAN_FP,
        policy_version=DEFAULT_POLICY.version,
    )


def test_w7_a_record_1_is_refused():
    record = {"record_version": "final-text-record-1"}
    review = review_final_text(record, _decision(), EVIDENCE)
    assert "record_version:final-text-record-1" in review.reasons


# --- W8: the generator emits only known kinds -------------------------------


def _parts():
    return [
        PlaceLine("description.route", ROUTE, ("p-1", "p-2", "r-1")),
        TemplateBlock("description.chapters_heading", HEADING),
        Chapter("chapter.1", 0, "Brindlemoor", ("p-1",), ("p-1",)),
        Chapter("chapter.2", 1215, "Quillhaven", ("p-2",), ("p-2",)),
        Chapter("chapter.3", 3725, "Quillhaven", ("p-2",), ("p-2",)),
        TemplateBlock("description.footer", FOOTER),
        Tag("tag.1", "Quillhaven", ("p-2",)),
        Tag("tag.2", template_id=SCENIC),
    ]


def test_w8_every_generated_surface_has_a_known_kind_and_agrees():
    visible = build_visible(_parts())
    assert {entry["kind"] for entry in visible} <= VISIBLE_KINDS
    assert [e["text"] for e in visible if e["kind"] == CHAPTER] == [
        "0:00 Brindlemoor",
        "20:15 Quillhaven",
        "1:02:05 Quillhaven",
    ]
    assert visible[1]["text"] == TEMPLATES[HEADING]
    assert visible[-1]["text"] == TEMPLATES[SCENIC]
    review = _review(*visible)
    assert review.verdict == AGREE, review.reasons
    _verify(review)


def test_w8_the_generator_cannot_emit_an_unregistered_template():
    with pytest.raises(KeyError):
        build_visible([TemplateBlock("description.footer", "x.y")])
    with pytest.raises(ValueError):
        build_visible([Tag("tag.1")])


def test_w8_an_unknown_kind_is_refused_by_the_review():
    entry = build_visible(_parts())[0] | {"kind": "free_text"}
    assert "surface_kind:description.route" in _review(entry).reasons


def test_the_templates_are_in_the_default_text_policy():
    assert dict(DEFAULT_TEXT_POLICY.templates) == {
        tid: template_hash(text) for tid, text in TEMPLATES.items()
    }


# --- template vocabulary: recommended, NOT fail-closed ----------------------

# A hash binds a template's bytes, not their meaning: a person editing a
# template in a Waypost PR could still write a place name into it. This test
# only makes that edit visible by refusing any word outside a closed list;
# the PR review of the template is what actually guards it.
TEMPLATE_VOCABULARY = frozenset(
    {"chapters", "subscribe", "for", "more", "drives", "scenic", "drive", "road"}
    | {"trip", "driving", "tour", "4k"}
)


def test_template_words_stay_in_the_closed_vocabulary():
    import re

    for tid, text in TEMPLATES.items():
        words = {w.casefold() for w in re.findall(r"[^\W_]+", text)}
        assert words <= TEMPLATE_VOCABULARY, (tid, words - TEMPLATE_VOCABULARY)


# --- A template id names its surface: tag.* only on a tag ---------------------


def test_a_template_block_with_a_tag_template_id_is_refused():
    review = _review(_template("description.footer", SCENIC))
    assert "template_unknown:description.footer:tag.scenic_drive" in review.reasons


def test_a_tag_with_a_description_template_id_is_refused():
    review = _review(_tag("tag.1", TEMPLATES[FOOTER], template_id=FOOTER))
    assert "template_unknown:tag.1:description.footer" in review.reasons


# --- A Short's chapters do not widen what its title must name ----------------


def test_a_shorts_chapter_outside_the_title_segment_agrees():
    facts = TitleFacts(
        format="short",
        baseline_title="Quillhaven #shorts",
        place=Place("Quillhaven", "p-2"),
    )
    snap = EvidenceSnapshot(as_of="2026-03-20T00:00:00")
    decision = record_to_dict(select_title(facts, snap, DEFAULT_POLICY))
    ost = {
        "surface": "shorts_ost",
        "kind": PLACE_LINE,
        "text": "Quillhaven",
        "evidence_ids": ["p-2"],
        "scope": "segment",
        "segment_places": ["p-2"],
    }
    chapter = _chapter("chapter.1", "0:00 Brindlemoor", ["p-1"], ["p-1"])
    record = {
        "record_version": RECORD_VERSION,
        "approval_hash": "c" * 64,
        "title": "Quillhaven #shorts",
        "recording_date": DATE,
        "date_evidence_id": "d-1",
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
        "route_places": [
            {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
            {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
        ],
        "region_claims": [],
        "visible": [ost, chapter],
    }
    review = review_final_text(record, decision, EVIDENCE)
    assert review.verdict == AGREE, review.reasons


def test_compose_description_keeps_order_and_leaves_ost_and_tags_out():
    from seo_manager.analytics.final_text_surfaces import (
        compose_description,
        compose_tags,
    )

    entries = [
        _line("ost", "Brindlemoor"),
        _line("description", "Brindlemoor to Quillhaven"),
        _chapter("chapter.1", "0:00 Brindlemoor", ["p-1"], ["p-1"]),
        _line("thumbnail", "Quillhaven"),
        _template("description.footer", FOOTER),
        _line("shorts_ost", "Quillhaven"),
        {"surface": "tag.1", "kind": TAG, "text": "scenic drive"},
        {"surface": "tag.2", "kind": TAG, "text": "road trip"},
        _line("descriptionx", "not a description line"),
    ]
    assert compose_description(entries) == (
        "Brindlemoor to Quillhaven\n0:00 Brindlemoor\nSubscribe for more drives."
    )
    assert compose_tags(entries) == ["scenic drive", "road trip"]
    assert compose_description([]) == ""
    assert compose_tags([]) == []


def test_compose_accepts_review_surfaces():
    from seo_manager.analytics.final_text_surfaces import (
        compose_description,
        compose_tags,
    )

    review = _review(_template("description.footer", FOOTER))
    assert compose_description(review.surfaces) == "Subscribe for more drives."
    assert compose_tags(review.surfaces) == []
