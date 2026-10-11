"""``load_cadence`` reads the tenant ``seo: cadence:`` block and fails closed."""

from datetime import time

import pytest

from seo_manager.core.profile import load_cadence
from seo_manager.scheduling import cadence as cadence_mod

from .conftest import BRAND_YAML


def _write(tmp_path, text):
    p = tmp_path / "brand.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_regions_days_and_per_day_times(cadence):
    assert cadence.lead_hours == 72 and cadence.ready_depth == 2
    assert cadence.days("north") == frozenset({6})
    assert cadence.days("south") == frozenset({1, 4})
    assert cadence.time_for("south", 1) == time(16, 0)  # region default
    assert cadence.time_for("south", 4) == time(15, 0)  # day_times override
    assert cadence.regions["south"].shorts_times == (time(15, 0), time(18, 0))
    assert cadence.superlong_day == 5


def test_cadence_module_reexports_the_one_loader():
    assert cadence_mod.load is load_cadence


def test_defaults_when_optional_keys_absent(tmp_path):
    p = _write(
        tmp_path,
        "seo:\n  cadence:\n    regions:\n      south: {dow: [tue], time: '16:00'}\n",
    )
    c = load_cadence(p)
    assert (c.lead_hours, c.ready_depth, c.superlong_day) == (72, 2, None)


def test_all_regions_view_is_the_union(cadence):
    assert cadence.days(None) == frozenset({1, 4, 6})


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("lead_hours: 72", "lead_hours: 72\n    bogus: 1", "bogus"),
        ('time: "14:00"', 'time: "14:00"\n        tiem: "1"', "tiem"),
        ("dow: [sun]", "dow: [funday]", "funday"),
        ('time: "14:00"', 'time: "2pm"', "2pm"),
        ("lead_hours: 72", "lead_hours: -1", "lead_hours"),
        ('day_times: {fri: "15:00"}', 'day_times: {sat: "15:00"}', "sat"),
        ("dow: [tue, fri]", "dow: [tue fri]", "tue fri"),
        ("dow: [sun]", "dow: [sunburn]", "sunburn"),
        ("superlong_day: sat", "superlong_day: saturnday", "saturnday"),
    ],
)
def test_unknown_or_invalid_keys_raise(tmp_path, old, new, match):
    with pytest.raises(ValueError, match=match):
        load_cadence(_write(tmp_path, BRAND_YAML.replace(old, new)))


@pytest.mark.parametrize(
    "text",
    [
        "seo: {}\n",
        "other: 1\n",
        "seo:\n  cadence:\n    regions: {}\n",
        "seo: [1]\n",
        "seo:\n  cadence:\n    regions: [south]\n",
    ],
)
def test_missing_block_or_empty_regions_raise(tmp_path, text):
    with pytest.raises(ValueError, match="cadence"):
        load_cadence(_write(tmp_path, text))


def test_unknown_region_raises(cadence):
    with pytest.raises(ValueError, match="mars"):
        cadence.days("mars")


def test_full_weekday_names_are_accepted(tmp_path):
    c = load_cadence(
        _write(tmp_path, BRAND_YAML.replace("dow: [sun]", "dow: [Sunday]"))
    )
    assert c.days("north") == frozenset({6})


def test_decided_shorts_times_overlay_the_yaml_seed(tmp_path, brand_yaml, monkeypatch):
    from datetime import datetime

    from seo_manager.analytics import cadence_decisions as cd

    db = tmp_path / "d.sqlite3"
    plain = load_cadence(brand_yaml, decisions_db=db)  # no row: the seed
    assert plain.regions["south"].shorts_times == (time(15, 0), time(18, 0))
    cd.append(
        db, knob="shorts_times", region="south", value=["12:00", "18:00"],
        prev=["15:00", "18:00"], status="ok", source="policy", reason_code="HOUR_SWAP",
        basis="t", applied_at=datetime(2026, 10, 9),
    )  # fmt: skip
    got = load_cadence(brand_yaml, decisions_db=db)
    assert got.regions["south"].shorts_times == (time(12, 0), time(18, 0))
    assert got.regions["north"].shorts_times == (time(18, 0),)
    assert load_cadence(brand_yaml).regions["south"].shorts_times == (
        time(15, 0), time(18, 0),
    )  # fmt: skip
    monkeypatch.setattr(cd, "_check_coupling", lambda *a: None)
    cd.append(
        db, knob="shorts_times", region="south", value=["noon"], prev=["12:00"],
        status="ok", source="policy", reason_code="X", basis="t",
        applied_at=datetime(2026, 10, 10),
    )  # fmt: skip
    with pytest.raises(ValueError, match="south"):
        load_cadence(brand_yaml, decisions_db=db)
