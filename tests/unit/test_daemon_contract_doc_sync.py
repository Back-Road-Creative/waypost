"""The daemon's evidence vocabulary is in its doc, or it is nowhere.

``continuous_optimization_daemon`` refuses to name a direction below an
impressions floor, labels which *kind* of window its two rates cover, and
records why a direction was or was not given. Those are contract facts: a
reader of a cycle's ``metrics`` cannot interpret ``below_floor`` or
``cumulative_to_date`` without them, and ``docs/continuous-optimization-daemon.md``
is where this repo states them.

Prose is a copy of the code, so it drifts. This test makes the code the truth
and the prose provably current: every ``EVIDENCE_*`` verdict, both window
kinds, and the floor constant's name must appear in the daemon's own section
of that doc -- sliced by heading, so an unrelated mention elsewhere in the
file cannot satisfy it.
"""

from pathlib import Path

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod

DAEMON_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "continuous-optimization-daemon.md"
)

#: The heading that opens the daemon's section. A rename here is a deliberate
#: act; the test failing on it is the point.
SECTION_HEADING = "## Evidence vocabulary"


def _daemon_section() -> str:
    text = DAEMON_DOC.read_text(encoding="utf-8")
    if SECTION_HEADING not in text:
        pytest.fail(
            f"{DAEMON_DOC.name} has no {SECTION_HEADING!r} section. The daemon's evidence "
            "floor, window kind and sufficiency verdicts are contract facts and "
            "belong there."
        )
    after = text.split(SECTION_HEADING, 1)[1]
    # Stop at the next top-level heading so a later section cannot lend it text.
    return after.split("\n## ", 1)[0]


def _evidence_verdicts() -> dict[str, str]:
    return {
        name: getattr(cod, name)
        for name in dir(cod)
        if name.startswith("EVIDENCE_") and isinstance(getattr(cod, name), str)
    }


def test_every_evidence_verdict_is_documented():
    section = _daemon_section()
    verdicts = _evidence_verdicts()
    assert verdicts, (
        "no EVIDENCE_* constants found -- the test is looking in the wrong module"
    )
    missing = sorted(value for value in verdicts.values() if value not in section)
    assert not missing, (
        f"evidence verdicts the daemon can emit but the daemon doc never names: {missing}. "
        "A verdict a reader cannot look up is the same silence this module exists to remove."
    )


def test_both_window_kinds_are_documented():
    section = _daemon_section()
    missing = [
        value
        for value in (cod.WINDOW_CUMULATIVE_TO_DATE, cod.WINDOW_BOUNDED)
        if value not in section
    ]
    assert not missing, (
        f"window kinds absent from the daemon doc: {missing}. Whether the two compared "
        "rates are disjoint periods or running averages changes what a movement means."
    )


def test_the_impressions_floor_is_named_not_just_its_number():
    section = _daemon_section()
    assert "MIN_IMPRESSIONS_FOR_DIRECTION" in section, (
        "The daemon doc must name the floor constant, so a reader changing it knows "
        "the prose describes that symbol rather than a number typed twice."
    )
