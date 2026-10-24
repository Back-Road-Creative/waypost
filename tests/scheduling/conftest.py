"""Shared fixtures: a two-region cadence (north Sun 14:00, south Tue 16:00 / Fri 15:00)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from seo_manager.core.profile import load_cadence
from seo_manager.scheduling.store import ScheduleStore

EST = ZoneInfo("America/New_York")
ANCHOR = datetime(2026, 6, 9, 9, 0, tzinfo=EST)  # Tuesday; next Sundays: 06-14, 06-21

BRAND_YAML = """\
seo:
  cadence:
    lead_hours: 72
    ready_depth: 2
    superlong_day: sat
    regions:
      north:
        dow: [sun]
        time: "14:00"
        shorts_times: ["18:00"]
      south:
        dow: [tue, fri]
        time: "16:00"
        day_times: {fri: "15:00"}
        shorts_times: ["15:00", "18:00"]
"""


@pytest.fixture
def brand_yaml(tmp_path):
    p = tmp_path / "brand.yaml"
    p.write_text(BRAND_YAML, encoding="utf-8")
    return p


@pytest.fixture
def cadence(brand_yaml):
    return load_cadence(brand_yaml)


@pytest.fixture
def store(tmp_path, cadence):
    return ScheduleStore(tmp_path / "schedule.sqlite3", EST, cadence.regions)


def at(date: str, hhmm: str = "14:00") -> datetime:
    h, m = hhmm.split(":")
    y, mo, d = (int(x) for x in date.split("-"))
    return datetime(y, mo, d, int(h), int(m), tzinfo=EST)
