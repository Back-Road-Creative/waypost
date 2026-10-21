"""Viewer-facing locality naming: only reliable place names reach prose."""

from seo_manager.core.locality import dedupe_places, viewer_localities


def _rec(name, match_type=None, place_class=None, addresstype=None, full_address=None):
    return {
        "name": name,
        "match_type": match_type,
        "place_class": place_class,
        "addresstype": addresstype,
        "full_address": full_address,
    }


ISLAND_ADDR = (
    "Seaside Road, Longwood, St. Helena Island, Beaufort County, South Carolina, 29920"
)


def test_vicinity_name_on_island_becomes_island_and_landmark_kept():
    out = viewer_localities(
        [
            _rec(
                "Longwood",
                match_type="residential",
                place_class="highway",
                full_address=ISLAND_ADDR,
            ),
            _rec("Fort Fremont", match_type="fort", place_class="historic"),
        ]
    )
    assert out.names == ["St. Helena Island", "Fort Fremont"]
    assert out.substitutions == {"Longwood": "St. Helena Island"}


def test_vicinity_name_without_island_is_dropped():
    out = viewer_localities(
        [
            _rec("Oceanmarsh Subdivision", match_type="house", place_class="building"),
            _rec("Milford", "town", "place"),
        ]
    )
    assert out.names == ["Milford"]
    assert out.substitutions == {"Oceanmarsh Subdivision": ""}


def test_census_place_kinds_kept():
    recs = [
        _rec(n, match_type=t, place_class="place")
        for n, t in [("A", "city"), ("B", "town"), ("C", "village"), ("D", "hamlet")]
    ]
    assert viewer_localities(recs).names == ["A", "B", "C", "D"]


def test_house_match_with_hamlet_address_is_vicinity():
    # the matched OSM object is a house: the hamlet is only its postal vicinity
    out = viewer_localities(
        [_rec("Tractville", match_type="house", addresstype="hamlet")]
    )
    assert out.names == []


def test_named_island_kept_and_no_facts_kept():
    out = viewer_localities(
        [_rec("Daufuskie Island", match_type="island"), _rec("Plainname")]
    )
    assert out.names == ["Daufuskie Island", "Plainname"]


def test_island_substitution_not_repeated_and_places_not_duplicated():
    out = viewer_localities(
        [
            _rec(
                "Tract One",
                match_type="house",
                full_address="x, Tract One, Big Island, County",
            ),
            _rec(
                "Tract Two",
                match_type="house",
                full_address="x, Tract Two, Big Island, County",
            ),
            _rec("Big Island", match_type="island"),
            _rec("Fort Z", place_class="historic"),
            _rec("fort z", place_class="historic"),
        ]
    )
    assert out.names == ["Big Island", "Fort Z"]


def test_plain_string_names_pass_through_deduped():
    assert viewer_localities(["Harrow", "Pellmere", "Harrow"]).names == [
        "Harrow",
        "Pellmere",
    ]


def test_dedupe_places_first_seen_case_insensitive():
    assert dedupe_places(["Fort Fremont", "Longwood", "fort fremont", "", None]) == [
        "Fort Fremont",
        "Longwood",
    ]


# Real records from the GMS reverse-geocode cache (Beaufort SC, 2026-02-20): the
# address carries Longwood / Fort Fremont as a HAMLET of a house or road match,
# and no island anywhere in the record.
LONGWOOD = {
    "name": "Longwood",
    "match_type": "residential",
    "place_class": "highway",
    "addresstype": "road",
    "full_address": "Seaside Road, Longwood, Beaufort County, South Carolina, 29920, United States",
    "entry_latitude": 32.3551,
    "entry_longitude": -80.5753,
}
FORT_FREMONT = {
    "name": "Fort Fremont",
    "match_type": "house",
    "place_class": "place",
    "addresstype": "place",
    "full_address": "27, Finch Road, Fort Fremont, Beaufort County, South Carolina, 29920, United States",
    "entry_latitude": 32.3048,
    "entry_longitude": -80.6413,
}


def test_real_beaufort_records_drop_longwood_keep_fort():
    out = viewer_localities([LONGWOOD, FORT_FREMONT])
    assert out.names == ["Fort Fremont"]
    assert out.substitutions == {"Longwood": ""}


def test_no_island_is_inferred_from_coordinates():
    # Census COUSUB "St. Helena Island" (a county division, tl_2026_45_cousub.zip)
    # also covers Ladys Island Drive points from the real geocode cache, so
    # coordinates cannot say which island a name lies on. A Lady's Island fix
    # (32.3937, -80.6727) must never come out as St. Helena Island.
    ladys = dict(
        LONGWOOD, name="Tractname", entry_latitude=32.3937, entry_longitude=-80.6727
    )
    out = viewer_localities([ladys])
    assert out.names == []
    assert out.substitutions == {"Tractname": ""}


def test_class_only_record_without_match_type_or_addresstype_is_kept():
    # a bare town dict (name + OSM class) gives no basis to call a name vicinity
    assert viewer_localities(
        [{"name": "Springfield", "place_class": "city"}]
    ).names == ["Springfield"]
