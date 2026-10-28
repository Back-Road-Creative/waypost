"""``collect run`` / ``collect report`` read a SOPS-encrypted token directly.

The plaintext lives only in a private temp file for the length of the command,
and a decrypt failure stops the command before any source is built. No network.
"""

from __future__ import annotations

import json
import stat

import pytest

from seo_manager import cli
from seo_manager.collection import credentials, sources
from seo_manager.collection.store import CollectionStore

TOKEN = {"refresh_token": "r-secret", "client_id": "c", "client_secret": "s"}


def fake_sops(info=TOKEN, error=""):
    calls = []

    def decrypt(path):
        calls.append(path)
        return (info, error) if info is not None else (None, error)

    decrypt.calls = calls
    return decrypt


def test_plain_path_passes_through_untouched(tmp_path):
    token = tmp_path / "t.json"
    with credentials.youtube_credentials(str(token), None) as path:
        assert path == token
    with credentials.youtube_credentials(None, None) as path:
        assert path is None


def test_sops_file_is_decrypted_to_a_private_file_removed_after(tmp_path, monkeypatch):
    decrypt = fake_sops()
    monkeypatch.setattr(credentials, "_sops_token", decrypt)
    enc = tmp_path / "t.json.enc"
    with credentials.youtube_credentials(None, str(enc)) as path:
        assert json.loads(path.read_text()) == TOKEN
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        kept = path
    assert decrypt.calls == [str(enc)]
    assert not kept.exists() and not kept.parent.exists()


def test_temp_file_is_removed_when_the_body_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_sops_token", fake_sops())
    with (
        pytest.raises(RuntimeError),
        credentials.youtube_credentials(None, str(tmp_path / "e")) as path,
    ):
        kept = path
        raise RuntimeError("boom")
    assert not kept.parent.exists()


def test_decrypt_failure_raises_without_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        credentials, "_sops_token", fake_sops(None, "verdict=sops-error rc=128")
    )
    with (
        pytest.raises(credentials.CredentialsError) as err,
        credentials.youtube_credentials(None, str(tmp_path / "e")),
    ):
        pytest.fail("body must not run")
    assert "rc=128" in str(err.value)


def test_decrypt_crash_is_reported_by_type_only(tmp_path, monkeypatch):
    def crash(path):
        raise FileNotFoundError("sops: r-secret")

    monkeypatch.setattr(credentials, "_sops_token", crash)
    with (
        pytest.raises(credentials.CredentialsError) as err,
        credentials.youtube_credentials(None, str(tmp_path / "e")),
    ):
        pass
    assert "FileNotFoundError" in str(err.value)
    assert "r-secret" not in str(err.value)


def test_run_hands_the_decrypted_file_to_the_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(credentials, "_sops_token", fake_sops())
    seen = {}

    def build(**kw):
        seen["path"] = kw["youtube_credentials"]
        seen["token"] = json.loads(kw["youtube_credentials"].read_text())
        return [], []

    monkeypatch.setattr(sources, "build_adapters", build)
    rc = cli.main(
        [
            "collect",
            "run",
            "--tenant",
            "gms",
            "--db",
            str(tmp_path / "c.db"),
            "--youtube-credentials-sops",
            str(tmp_path / "t.enc"),
        ]
    )
    capsys.readouterr()
    assert rc == 2  # nothing built, so nothing configured
    assert seen["token"] == TOKEN
    assert not seen["path"].exists()


def test_run_decrypt_failure_exits_2_before_any_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(credentials, "_sops_token", fake_sops(None, "rc=1"))
    monkeypatch.setattr(
        sources, "build_adapters", lambda **kw: pytest.fail("must not build")
    )
    rc = cli.main(
        [
            "collect",
            "run",
            "--tenant",
            "gms",
            "--db",
            str(tmp_path / "c.db"),
            "--youtube-credentials-sops",
            str(tmp_path / "t.enc"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2 and "collect run" in err and "rc=1" in err
    assert not (tmp_path / "c.db").exists()


@pytest.mark.parametrize("action", ["run", "report"])
def test_plain_and_sops_flags_are_exclusive(action, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "collect",
                action,
                "--tenant",
                "gms",
                "--youtube-credentials",
                "a.json",
                "--youtube-credentials-sops",
                "a.enc",
            ]
        )
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_report_uses_the_decrypted_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(credentials, "_sops_token", fake_sops())
    db = tmp_path / "c.db"
    CollectionStore(db).init()
    rc = cli.main(
        [
            "collect",
            "report",
            "--tenant",
            "gms",
            "--db",
            str(db),
            "--json",
            "--youtube-credentials-sops",
            str(tmp_path / "t.enc"),
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    # no videos stored, but credentials are configured: "not_needed"
    assert data["video_text"]["state"] == "not_needed"


def test_report_decrypt_failure_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(credentials, "_sops_token", fake_sops(None, "rc=1"))
    db = tmp_path / "c.db"
    CollectionStore(db).init()
    rc = cli.main(
        [
            "collect",
            "report",
            "--tenant",
            "gms",
            "--db",
            str(db),
            "--youtube-credentials-sops",
            str(tmp_path / "t.enc"),
        ]
    )
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and "collect report" in out.err
