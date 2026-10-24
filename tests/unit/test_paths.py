"""The data-home convention: env override, XDG, then ~/.local/share."""

from pathlib import Path

import pytest

from seo_manager import cli, paths


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SEO_MANAGER_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_falls_back_to_the_local_share_directory(clean_env):
    assert paths.data_home() == clean_env / "home" / ".local" / "share" / "seo-manager"


def test_xdg_data_home_is_used_when_set(clean_env, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(clean_env / "xdg"))
    assert paths.data_home() == clean_env / "xdg" / "seo-manager"


def test_explicit_variable_beats_xdg(clean_env, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(clean_env / "xdg"))
    monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(clean_env / "explicit"))
    assert paths.data_home() == clean_env / "explicit"


def test_data_home_is_not_created_by_resolving_it(clean_env):
    assert not paths.data_home().exists()


def test_report_without_videos_yaml_names_the_missing_option(capsys):
    assert cli.main(["report"]) == 2
    err = capsys.readouterr().err
    assert "--videos-yaml is not configured" in err


def test_report_without_a_ledger_names_the_ledger_option(
    clean_env, monkeypatch, capsys
):
    monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(clean_env / "d"))
    videos = clean_env / "videos.yaml"
    videos.write_text("videos: []\n", encoding="utf-8")
    assert cli.main(["report", "--videos-yaml", str(videos)]) == 2
    err = capsys.readouterr().err
    assert "--ledger is not configured" in err
    assert not (clean_env / "d").exists()


def test_report_with_a_missing_ledger_names_the_path(clean_env, capsys):
    videos = clean_env / "videos.yaml"
    videos.write_text("videos: []\n", encoding="utf-8")
    missing = clean_env / "missing.jsonl"
    assert (
        cli.main(["report", "--videos-yaml", str(videos), "--ledger", str(missing)])
        == 2
    )
    err = capsys.readouterr().err
    assert "--ledger" in err
    assert str(missing) in err


def test_sync_dry_run_without_a_ledger_does_not_invent_a_location(
    clean_env, monkeypatch, capsys
):
    monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(clean_env / "d"))
    assert cli.main(["sync", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--ledger" in out
    assert str(clean_env / "d") not in out


def test_sync_ledger_option_is_named_in_the_output(clean_env, capsys):
    other = clean_env / "other.jsonl"
    assert cli.main(["sync", "--dry-run", "--ledger", str(other)]) == 0
    assert str(other) in capsys.readouterr().out
