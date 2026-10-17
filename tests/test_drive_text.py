"""Drive text: parameterized templates, the layout and the tag set.

Every place is invented (synthetic). A parameterized template is a registered
skeleton with typed slots; a slot never carries a free word, and a place slot
is a cited evidence id backed like a place line.
"""

import dataclasses
import math

import pytest
from drive_text_synthetic import PLAN, SRC, TITLE, _edit, _find, _review, golden_entries

from seo_manager.analytics.drive_templates import PARAMS, TAG_ORDER
from seo_manager.analytics.final_text_params import ParamTemplate, Slot
from seo_manager.analytics.final_text_review import (
    AGREE,
    DEFAULT_TEXT_POLICY,
    FinalTextReviewRefused,
    verify_review,
)
from seo_manager.analytics.final_text_surfaces import ParamBlock, build_visible


def test_w10_a_review_4_record_is_refused_at_upload():
    review = _review(golden_entries())
    from seo_manager.analytics.final_text_review import _fingerprint_of, review_to_dict

    body = review_to_dict(review)
    body["review_version"] = "final-text-review-4"
    body["fingerprint"] = _fingerprint_of(body)
    with pytest.raises(FinalTextReviewRefused) as caught:
        verify_review(
            body, final_title=TITLE, visible={}, source_fingerprint=SRC,
            plan_fingerprint=PLAN, policy_version=review.decision_policy_version,
        )  # fmt: skip
    assert caught.value.code == "stale:review_version"


# --- W10-1: the skeleton is hashed, markers intact -----------------------------

LOCATION = "description.location"


@pytest.mark.parametrize(
    "skeleton",
    [
        "\U0001f4cd Location: {place} via {road} ",  # trailing space
        "\U0001f4cd Location: {place} via {road}\n",  # trailing newline
        "\U0001f4cd  Location: {place} via {road}",  # doubled space
        "\U0001f4cd Locations: {place} via {road}",  # one letter
    ],
)
def test_w10_1_one_changed_skeleton_byte_is_refused(skeleton):
    entries = _edit(LOCATION, skeleton=skeleton)
    assert (
        f"template_changed:{LOCATION}:description.location.road"
        in _review(entries).reasons
    )


def test_w10_1_an_unknown_skeleton_id_is_refused():
    entries = _edit(LOCATION, template_id="description.location.nowhere")
    assert (
        f"template_unknown:{LOCATION}:description.location.nowhere"
        in _review(entries).reasons
    )


def test_w10_1_a_known_id_carrying_another_templates_hash_is_refused():
    other = PARAMS["description.location.place"]
    entries = _edit(LOCATION, skeleton=other.skeleton)
    assert (
        f"template_changed:{LOCATION}:description.location.road"
        in _review(entries).reasons
    )


def test_w10_1_filled_text_that_is_not_the_skeleton_filled_is_refused():
    entries = _edit(
        LOCATION, text="\U0001f4cd Location: Quillhaven via Tidewater Road!"
    )
    assert f"param_text:{LOCATION}" in _review(entries).reasons


# --- W10-2: slot typing --------------------------------------------------------

DISTANCE = "description.distance"


def _slots(surface, **changes):
    slots = dict(_find(golden_entries(), surface)["slots"], **changes)
    return _edit(surface, slots=slots)


@pytest.mark.parametrize(
    "value", ["several", "47", float("nan"), math.inf, -3, True, None]
)
def test_w10_2_a_number_slot_takes_only_a_finite_non_negative_number(value):
    reasons = _review(_slots(DISTANCE, km=value)).reasons
    assert f"param_slot:{DISTANCE}:km" in reasons


def test_w10_2_a_word_in_a_numeric_slot_is_refused_even_when_the_text_agrees():
    entries = _slots(DISTANCE, km="six")
    _find(entries, DISTANCE)["text"] = "\U0001f6e3️ Distance: six km (29 mi)"
    reasons = _review(entries).reasons
    assert f"param_slot:{DISTANCE}:km" in reasons


def test_w10_2_text_that_hides_extra_words_behind_a_number_is_refused():
    entries = _edit(DISTANCE)
    _find(entries, DISTANCE)["text"] = "\U0001f6e3️ Distance: 47 km; buy now (29 mi)"
    assert f"param_text:{DISTANCE}" in _review(entries).reasons


def test_w10_2_a_unit_outside_the_slots_declared_units_is_refused():
    entries = _edit(
        "description.distance",
        template_id="description.distance.single",
        skeleton=PARAMS["description.distance.single"].skeleton,
        slots={"distance": 29, "unit": "furlongs"},
        text="\U0001f6e3️ Distance: 29 furlongs",
    )
    assert f"param_slot:{DISTANCE}:unit" in _review(entries).reasons


@pytest.mark.parametrize("value", ["about an hour", "1h 75m", "5 minutes", "", 90])
def test_w10_2_a_duration_slot_takes_only_the_registered_format(value):
    reasons = _review(
        _slots("description.duration", final=value, source="2h 5m")
    ).reasons
    assert "param_slot:description.duration:final" in reasons


@pytest.mark.parametrize("value", ["Northwest-ish", "westbound", "WESTBOUND", ""])
def test_w10_2_an_enum_slot_takes_only_registered_members_case_sensitively(value):
    # Case is not folded: "westbound" is not "Westbound".
    reasons = _review(_slots("description.heading", heading=value)).reasons
    assert "param_slot:description.heading:heading" in reasons


def test_w10_2_a_place_slot_must_cite_its_evidence_id():
    entries = _edit("description.location", evidence_ids=["p-2"])
    assert any(
        r.startswith("param_place:description.location")
        for r in _review(entries).reasons
    )


def test_w10_2_a_place_slot_backed_by_evidence_off_the_route_is_refused():
    parts = [ParamBlock("description.location", "description.location.place",
                        {"place": "p-4"}, {"p-4": "Fenwick Reach"})]  # fmt: skip
    entries = [e for e in golden_entries() if e["surface"] != "description.location"]
    entries.insert(3, build_visible(parts)[0])
    assert "place_not_on_route:description.location:p-4" in _review(entries).reasons


def test_w10_2_a_place_slot_citing_no_evidence_is_refused():
    entries = _slots("description.location", road="x-9")
    assert any(
        r.startswith("param_place:description.location")
        for r in _review(entries).reasons
    )


def test_w10_2_a_string_catch_all_slot_cannot_be_registered():
    with pytest.raises(ValueError, match="slot_type_unregistered"):
        Slot("note", "string")
    with pytest.raises(ValueError, match="slot_type_unregistered"):
        ParamTemplate("description.x", "{note}", (Slot("note", "text"),))
    assert all(s.type != "string" for t in PARAMS.values() for s in t.slots)


# --- W10-3: tags -----------------------------------------------------------------


def _tag(surface, text, ids=(), template_id=None):
    entry = {"surface": surface, "kind": "tag", "text": text, "evidence_ids": list(ids)}
    return entry | ({"template_id": template_id} if template_id else {})


def _with_tags(*tags):
    return [e for e in golden_entries() if e["kind"] != "tag"] + list(tags)


def test_w10_3_registered_non_place_tags_and_backed_place_tags_agree():
    # Registered tags come first; cited place tags follow them (W10c-5).
    tags = [_tag("tag.1", "dashcam", template_id="tag.dashcam"),
            _tag("tag.2", "Quillhaven", ["p-2"])]  # fmt: skip
    assert _review(_with_tags(*tags)).verdict == AGREE


def test_w10_3_the_same_words_unregistered_are_refused():
    review = _review(_with_tags(_tag("tag.1", "dashcam", ["p-2"])))
    assert "name_unbacked:tag.1:dashcam" in review.reasons
    review = _review(_with_tags(_tag("tag.1", "dashcam", template_id="tag.unlisted")))
    assert "template_unknown:tag.1:tag.unlisted" in review.reasons


def test_w10_3_a_tag_naming_an_unbacked_place_is_refused():
    review = _review(_with_tags(_tag("tag.1", "Fenwick Reach", ["p-4"])))
    assert "place_not_on_route:tag.1:p-4" in review.reasons


def test_w10_3_registered_tags_keep_the_registered_order():
    ids = ["tag.dashcam", "tag.drive"]
    assert TAG_ORDER.index(ids[0]) > TAG_ORDER.index(ids[1])
    tags = [_tag(f"tag.{n}", t[4:].replace("_", " "), template_id=t)
            for n, t in enumerate(ids, 1)]  # fmt: skip
    assert "tag_order" in _review(_with_tags(*tags)).reasons


# --- W10-4: the description is the layout's registered blocks, in order ----------


def _free_line(text, surface="description.note"):
    return {"surface": surface, "kind": "place_line", "text": text,
            "evidence_ids": ["p-2"], "scope": "whole"}  # fmt: skip


@pytest.mark.parametrize("position", [0, 4, 9, -3])
def test_w10_4_a_free_line_anywhere_is_refused(position):
    entries = golden_entries()
    entries.insert(position, _free_line("Quillhaven"))
    assert "layout_free_line:description.note" in _review(entries).reasons


def test_w10_4_a_rewritten_hook_line_is_refused():
    entries = [e for e in golden_entries() if e["surface"] != "description.hook"]
    entries.insert(0, _free_line("Quillhaven drive", "description.hook"))
    assert "layout_free_line:description.hook" in _review(entries).reasons


def test_w10_4_a_missing_required_block_is_refused():
    entries = [e for e in golden_entries() if e["surface"] != "description.cta"]
    assert "layout_missing:cta" in _review(entries).reasons


def test_w10_4_a_block_out_of_order_is_refused():
    entries = golden_entries()
    entries[4], entries[5] = entries[5], entries[4]
    assert any(r.startswith("layout_order:") for r in _review(entries).reasons)


def test_w10_4_a_block_under_another_blocks_surface_is_refused():
    entries = _edit("description.cta", template_id="description.site",
                    text="\U0001f30d https://gomoveshift.com\n")  # fmt: skip
    assert "layout_free_line:description.cta" in _review(entries).reasons


def test_w10_4_an_unknown_layout_is_refused():
    assert (
        "layout_unknown:made-up" in _review(golden_entries(), layout="made-up").reasons
    )


# --- W10-5: build_visible cannot emit an invalid surface ---------------------------


def test_w10_5_a_raw_string_for_a_numeric_slot_raises_at_build_time():
    bad = ParamBlock("description.distance", "description.distance.both",
                     {"km": "47", "mi": 29})  # fmt: skip
    with pytest.raises(ValueError, match="slot_value"):
        build_visible([bad])


def test_w10_5_a_raw_string_for_an_enum_slot_raises_at_build_time():
    bad = ParamBlock("description.heading", "description.heading.compass",
                     {"heading": "Due west"})  # fmt: skip
    with pytest.raises(ValueError, match="slot_value"):
        build_visible([bad])


def test_w10_5_a_place_that_is_not_cited_cannot_be_built():
    bad = ParamBlock("description.location", "description.location.place",
                     {"place": "Quillhaven"}, {"p-2": "Quillhaven"})  # fmt: skip
    with pytest.raises(ValueError, match="place_slot"):
        build_visible([bad])


# --- W10-7: the template set is versioned -----------------------------------------


def _changed_policy():
    changed = tuple(
        (tid, "0" * 64 if tid == "description.heading.compass" else digest)
        for tid, digest in DEFAULT_TEXT_POLICY.drive_templates
    )
    return dataclasses.replace(
        DEFAULT_TEXT_POLICY,
        drive_templates=changed,
        drive_version="drive-templates-next",
    )


def test_w10_7_a_record_written_under_policy_n_is_refused_under_n_plus_1():
    entries = golden_entries()
    assert _review(entries).verdict == AGREE
    later = _review(entries, policy=_changed_policy())
    assert (
        "template_changed:description.heading:description.heading.compass"
        in later.reasons
    )


def test_w10_7_a_review_made_under_policy_n_is_stale_under_n_plus_1():
    review = _review(golden_entries())
    with pytest.raises(FinalTextReviewRefused) as caught:
        verify_review(
            review, final_title=TITLE, visible={}, source_fingerprint=SRC,
            plan_fingerprint=PLAN, policy_version=review.decision_policy_version,
            text_policy=_changed_policy(),
        )  # fmt: skip
    assert "stale:text_policy" in caught.value.reasons


# --- W10c-3: a drive review must name its layout ----------------------------------


def _without_layout(entries):
    return _review(entries, layout=None).reasons


def test_w10c_3_a_drive_review_without_a_layout_is_refused():
    assert "layout_required" in _without_layout(golden_entries())


def test_w10c_3_an_extra_free_line_without_a_layout_is_refused():
    entries = golden_entries()
    entries.insert(4, _free_line("Quillhaven"))
    assert "layout_required" in _without_layout(entries)


def test_w10c_3_a_dropped_cta_without_a_layout_is_refused():
    entries = [e for e in golden_entries() if e["surface"] != "description.cta"]
    assert "layout_required" in _without_layout(entries)


def test_w10c_3_swapped_blocks_without_a_layout_are_refused():
    entries = golden_entries()
    entries[4], entries[5] = entries[5], entries[4]
    assert "layout_required" in _without_layout(entries)


def test_w10c_3_a_review_with_no_drive_surface_needs_no_layout():
    entries = [_free_line("Quillhaven", "description.place")]
    assert "layout_required" not in _without_layout(entries)


# --- W10c-5: tag order and the year tag -------------------------------------------


def _tag_surfaces(entries):
    return [e["surface"] for e in entries if e["kind"] == "tag"]


def _move_tag(surface, before):
    entries = golden_entries()
    moved = _find(entries, surface)
    entries.remove(moved)
    entries.insert(entries.index(_find(entries, before)), moved)
    return entries


def test_w10c_5_tag_order_follows_gms_base_place_year_month_season():
    # GMS longform_metadata: base tags, then _location_tags (place parts,
    # "<place> drive", year, then _seasonal_tags month, season, season drive).
    from seo_manager.analytics.drive_templates import BASE_TAGS

    base = len(BASE_TAGS)
    assert TAG_ORDER[base : base + 3] == ("tag.place", "tag.place_drive", "tag.year")
    assert TAG_ORDER[base + 3 : base + 15] == tuple(
        f"tag.month_{m}" for m in range(1, 13)
    )
    assert TAG_ORDER[base + 15].startswith("tag.season_")
    assert TAG_ORDER[-2].endswith("_drive") and TAG_ORDER[-1] == "tag.phrase"


def test_w10c_5_the_golden_tags_in_registered_order_agree():
    entries = golden_entries()
    assert _tag_surfaces(entries)[12:] == [
        "tag.place.1", "tag.place.2", "tag.place_drive.1", "tag.place_drive.2",
        "tag.year", "tag.month_9", "tag.season_autumn", "tag.season_autumn_drive",
    ]  # fmt: skip
    assert _find(entries, "tag.year")["kind"] == "tag"
    assert _find(entries, "tag.place_drive.1")["text"] == "Brindlemoor drive"
    assert _review(entries).verdict == AGREE


@pytest.mark.parametrize(
    ("moved", "before"),
    [("tag.year", "tag.1"), ("tag.month_9", "tag.year"), ("tag.year", "tag.place.1"),
     ("tag.place_drive.1", "tag.place.1"), ("tag.place.1", "tag.1"),
     ("tag.season_autumn_drive", "tag.season_autumn")],
)  # fmt: skip
def test_w10c_5_one_reordered_tag_is_refused(moved, before):
    assert "tag_order" in _review(_move_tag(moved, before)).reasons


@pytest.mark.parametrize(
    "tag", ["tag.1", "tag.year", "tag.month_9", "tag.season_autumn"]
)
def test_w10c_5_a_non_repeatable_tag_sent_twice_is_refused(tag):
    # The copy sits right after the original, so only the repeat check sees it.
    entries = golden_entries()
    original = _find(entries, tag)
    entries.insert(entries.index(original) + 1, dict(original, surface=tag + ".again"))
    assert "tag_order" in _review(entries).reasons


def test_w10c_5_a_place_drive_tag_must_cite_a_route_place():
    entries = _edit(
        "tag.place_drive.1",
        slots={"place": "p-4"},
        evidence_ids=["p-4"],
        text="Fenwick Reach drive",
    )
    assert "place_not_on_route:tag.place_drive.1:p-4" in _review(entries).reasons


@pytest.mark.parametrize("value", ["twenty", "2026", 1899, 2201, 2026.5, True])
def test_w10c_5_a_year_tag_takes_only_a_year_number(value):
    entries = _edit("tag.year", slots={"year": value}, text=str(value))
    assert "param_slot:tag.year:year" in _review(entries).reasons


def test_w10c_5_a_year_tag_must_be_the_recording_year():
    entries = _edit("tag.year", slots={"year": 2025}, text="2025")
    assert "tag_year_unbacked:tag.year" in _review(entries).reasons


def test_w10c_5_a_year_template_under_a_description_surface_is_refused():
    entries = _edit("tag.year", kind="param_template")
    assert "template_unknown:tag.year:tag.year" in _review(entries).reasons


def test_w10c_5_a_description_template_sent_as_a_tag_is_refused():
    entries = _edit("description.distance", kind="tag")
    reasons = _review(entries).reasons
    assert "template_unknown:description.distance:description.distance.both" in reasons


def _with_phrase(position=None, place="p-2", text="Quillhaven"):
    entries = golden_entries()
    phrase = build_visible(
        [ParamBlock("tag.phrase.1", "tag.phrase", {"place": place}, {place: text})]
    )[0]
    entries.insert(len(entries) if position is None else position, phrase)
    return entries


def test_w10c_5_a_cited_phrase_tag_after_the_season_tags_agrees():
    # GMS appends _phrase_tags after _location_tags (longform_metadata).
    assert TAG_ORDER[-1] == "tag.phrase"
    assert _review(_with_phrase()).verdict == AGREE


def test_w10c_5_a_phrase_tag_before_the_year_is_refused():
    entries = _with_phrase()
    year = entries.index(_find(entries, "tag.year"))
    assert "tag_order" in _review(_with_phrase(year)).reasons


def test_w10c_5_a_phrase_tag_off_the_route_is_refused():
    reasons = _review(_with_phrase(place="p-4", text="Fenwick Reach")).reasons
    assert "place_not_on_route:tag.phrase.1:p-4" in reasons


# --- W10c-6: every body and footer shape GMS emits, closed and typed ------------

PLAYLIST = "https://www.youtube.com/playlist?list=PLsyntheticAAAA0001"
ALLOW = dataclasses.replace(
    DEFAULT_TEXT_POLICY, playlist_allowlist=("PLsyntheticAAAA0001",)
)
_NAMES = {"p-1": "Brindlemoor", "p-2": "Quillhaven", "p-3": "Oceanmarsh",
          "p-4": "Fenwick Reach", "r-1": "Harrow County", "rd-1": "Tidewater Road"}  # fmt: skip


def _block(surface, tid, slots):
    places = {v: _NAMES[v] for v in slots.values() if v in _NAMES}
    return build_visible([ParamBlock(surface, tid, slots, places)])[0]


def _put(entry, after=None, replace=None):
    entries = golden_entries()
    if replace:
        entries[entries.index(_find(entries, replace))] = entry
    else:
        entries.insert(entries.index(_find(entries, after)) + 1, entry)
    return entries


_BODY = "description.body"
SHAPES = [
    (_BODY, "description.body.arc.0", {"a": "p-1", "b": "p-2"}, {"replace": _BODY}),
    (_BODY, "description.body.arc.1", {"a": "p-1", "b": "p-2", "p1": "p-3"},
     {"replace": _BODY}),
    (_BODY, "description.body.towns.2", {"t1": "p-1", "t2": "p-2"}, {"replace": _BODY}),
    (_BODY, "description.body.towns_roads.2.1", {"t1": "p-1", "t2": "p-2", "r1": "rd-1"},
     {"replace": _BODY}),
    ("description.roads", "description.roads.1", {"r1": "rd-1"}, {"after": _BODY}),
    ("description.states", "description.states.2", {"s1": "p-1", "s2": "p-2"},
     {"after": _BODY}),
    ("description.features", "description.features.1", {"f1": "p-3"}, {"after": _BODY}),
    ("description.through", "description.through.1.more", {"p1": "r-1", "overflow": 3},
     {"replace": "description.through"}),
    ("description.playlist", "description.playlist.full",
     {"region": "Ireland", "url": PLAYLIST}, {"after": "description.cta"}),
    ("description.cadence", "description.cadence.weekly",
     {"region": "USA", "weekday": "Sunday"}, {"after": "description.cta"}),
]  # fmt: skip


@pytest.mark.parametrize(("surface", "tid", "slots", "where"), SHAPES)
def test_w10c_6_each_registered_gms_shape_agrees(surface, tid, slots, where):
    entries = _put(_block(surface, tid, slots), **where)
    assert _review(entries, policy=ALLOW).verdict == AGREE


def test_w10c_6_the_gms_body_skeletons_are_the_gms_sentences():
    skel = {t: PARAMS[t].skeleton for t in PARAMS}
    assert skel["description.body.arc.0"] == "Started in {a}, ended in {b}."
    assert skel["description.body.arc.2"] == (
        "Started in {a}, ended in {b}, passing through {p1}, {p2}."
    )
    assert (
        skel["description.body.towns_roads.1.2"]
        == "Driving through {t1} on {r1}, {r2}."
    )
    assert skel["description.roads.1"] == "Roads traveled: {r1}."
    assert skel["description.states.3"] == "Spans 3 states: {s1}, {s2}, {s3}."
    assert skel["description.features.2"] == "Highlights along the route: {f1}, {f2}."
    assert skel["description.playlist.full"] == "▶ Full {region} playlist: {url}"
    assert skel["description.cadence.weekly"] == "New {region} drive every {weekday}"
    # Lists past GMS's caps have no template: the generator cannot exceed them.
    assert "description.roads.6" not in PARAMS
    assert "description.body.arc.6" not in PARAMS
    assert "description.features.6" not in PARAMS


def _playlist(url, policy=ALLOW):
    block = _block("description.playlist", "description.playlist.full",
                   {"region": "Ireland", "url": PLAYLIST})  # fmt: skip
    block["slots"]["url"] = url
    block["text"] = block["text"].replace(PLAYLIST, str(url))
    return _review(_put(block, after="description.cta"), policy=policy).reasons


# e5 mutation 1: the allowlist is ignored.
def test_w10c_6_a_well_formed_playlist_off_the_allowlist_is_refused():
    other = "https://www.youtube.com/playlist?list=PLsyntheticBBBB0002"
    assert "param_slot:description.playlist:url" in _playlist(other)
    # No allowlist configured: every playlist is refused (fail closed).
    assert "param_slot:description.playlist:url" in _playlist(
        PLAYLIST, DEFAULT_TEXT_POLICY
    )


# e5 mutation 5: the playlist slot accepts free text.
@pytest.mark.parametrize("url", [
    "see the playlist", "https://example.invalid/playlist?list=PLsyntheticAAAA0001",
    PLAYLIST + "&x=1", "http://www.youtube.com/playlist?list=PLsyntheticAAAA0001", 7,
])  # fmt: skip
def test_w10c_6_a_playlist_slot_takes_only_a_youtube_playlist_url(url):
    assert "param_slot:description.playlist:url" in _playlist(url)


# e5 mutation 2: the overflow count accepts a word.
@pytest.mark.parametrize("value", ["three", 0, 2.5, True])
def test_w10c_6_an_overflow_count_takes_only_a_whole_number(value):
    block = _block("description.through", "description.through.1.more",
                   {"p1": "r-1", "overflow": 3})  # fmt: skip
    block["slots"]["overflow"] = value
    block["text"] = block["text"].replace("+3", f"+{value}")
    entries = _put(block, replace="description.through")
    assert "param_slot:description.through:overflow" in _review(entries).reasons


# e5 mutation 4: the cadence enum accepts anything.
@pytest.mark.parametrize("day", ["Someday", "sunday", "Sunday!", ""])
def test_w10c_6_a_cadence_takes_only_a_weekday(day):
    block = _block("description.cadence", "description.cadence.weekly",
                   {"region": "USA", "weekday": "Sunday"})  # fmt: skip
    block["slots"]["weekday"] = day
    block["text"] = block["text"].replace("Sunday", day)
    entries = _put(block, after="description.cta")
    assert "param_slot:description.cadence:weekday" in _review(entries).reasons


def test_w10c_6_the_footer_sits_between_cta_and_site():
    block = _block("description.playlist", "description.playlist.full",
                   {"region": "Ireland", "url": PLAYLIST})  # fmt: skip
    entries = _put(block, after="description.site")
    assert "layout_order:description.playlist" in _review(entries, policy=ALLOW).reasons


def test_w10c_6_a_body_shape_naming_an_unbacked_place_is_refused():
    entries = _put(
        _block(_BODY, "description.body.arc.1", {"a": "p-1", "b": "p-2", "p1": "p-4"}),
        replace=_BODY,
    )
    assert "place_not_on_route:description.body:p-4" in _review(entries).reasons
