"""Operator-facing scheduling behavior, using only disposable tenant state."""

import json

import pytest

from seo_manager.cli import build_parser, main
from seo_manager.scheduling import claim as claims

from .conftest import at


@pytest.mark.parametrize("position", ["before", "after", "split"])
def test_shared_options_work_on_either_side_of_action(
    position, brand_yaml, tmp_path, monkeypatch, capsys
):
    db = tmp_path / "schedule.sqlite3"
    monkeypatch.setenv("SEO_SCHEDULE_DB", str(tmp_path / "wrong.sqlite3"))
    brand = ["--brand-yaml", str(brand_yaml)]
    storage = ["--db", str(db), "--init"]
    action = ["ready-depth", "--region", "north"]
    args = {
        "before": [*brand, *storage, *action],
        "after": [*action, *brand, *storage],
        "split": [*brand, *action, *storage],
    }[position]
    assert main(["schedule", *args]) == 0
    assert json.loads(capsys.readouterr().out)["depth"] == 0
    assert db.exists() and not (tmp_path / "wrong.sqlite3").exists()


def test_action_option_overrides_prefix_without_erasing_other_options():
    args = build_parser().parse_args(
        [
            "schedule",
            "--db",
            "first",
            "--brand-yaml",
            "brand.yaml",
            "--decisions-db",
            "decisions.sqlite3",
            "--init",
            "report",
            "--db",
            "last",
        ]
    )
    assert args.db == "last"
    assert args.brand_yaml == "brand.yaml"
    assert args.decisions_db == "decisions.sqlite3"
    assert args.init is True


def test_claim_explains_an_empty_queue(brand_yaml, tmp_path, capsys):
    assert (
        main(
            [
                "schedule",
                "--brand-yaml",
                str(brand_yaml),
                "--db",
                str(tmp_path / "schedule.sqlite3"),
                "--init",
                "claim",
                "--region",
                "north",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "claim": None,
        "reason": "no_ready_drive",
    }


def test_claim_explains_a_slot_outside_the_window(store, cadence):
    store.register_ready("route", "north", 1, mirror_sha="abc")
    result = claims.claim_or_reason(
        store, cadence, "north", now=at("2026-06-12"), lead_hours=0
    )
    assert result.reason == "outside_lead_window"
    assert result.next_slot == at("2026-06-14")
    assert store.bookings() == [] and store.drive("route").status == "ready"
    assert (
        claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=0)
        is None
    )


def test_cli_names_next_slot_without_booking(store, brand_yaml, capsys):
    store.register_ready("route", "north", 1, mirror_sha="abc")
    assert (
        main(
            [
                "schedule",
                "claim",
                "--brand-yaml",
                str(brand_yaml),
                "--db",
                str(store.path),
                "--region",
                "north",
                "--now",
                "2026-06-12T14:00:00-04:00",
                "--lead-hours",
                "0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "claim": None,
        "reason": "outside_lead_window",
        "next_slot": at("2026-06-14").isoformat(),
    }
    assert store.bookings() == [] and store.drive("route").status == "ready"


def test_missing_database_dry_run_has_a_reason_and_creates_nothing(store, cadence):
    result = claims.claim_or_reason(store, cadence, "north", dry_run=True)
    assert result.reason == "missing_database"
    assert not store.path.exists()
    assert not store.path.with_name(store.path.name + ".lock").exists()


def test_claim_reason_api_keeps_success_and_idempotency(store, cadence):
    store.register_ready("route", "north", 1, mirror_sha="abc")
    first = claims.claim_or_reason(store, cadence, "north", now=at("2026-06-12"))
    again = claims.claim(store, cadence, "north", now=at("2026-06-12"))
    assert isinstance(first, claims.Claim)
    assert first.claim_id == again.claim_id and again.already_claimed
    assert len(store.bookings()) == 1


def test_bad_date_remains_a_usage_error_without_booking(store, brand_yaml, capsys):
    store.init()
    assert (
        main(
            [
                "schedule",
                "--brand-yaml",
                str(brand_yaml),
                "--db",
                str(store.path),
                "claim",
                "--region",
                "north",
                "--now",
                "bad",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "seo-manager schedule claim:" in output.err
    assert "Traceback" not in output.err and not output.out
    assert store.bookings() == []
