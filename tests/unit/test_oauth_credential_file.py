"""OAuth output stays private and an unsuccessful save preserves the old file."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from seo_manager.analytics.youtube_sync import InstalledAppFlow, YouTubeAnalyticsSync


@pytest.fixture
def oauth(monkeypatch):
    credentials = Mock()
    credentials.to_json.return_value = '{"token": "synthetic"}'
    flow = Mock()
    flow.run_local_server.return_value = credentials
    monkeypatch.setattr(
        InstalledAppFlow, "from_client_secrets_file", Mock(return_value=flow)
    )
    return credentials


def save(path):
    return YouTubeAnalyticsSync.create_credentials_from_flow(
        "synthetic-client.json", str(path)
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
@pytest.mark.parametrize("existing", [False, True])
def test_saved_credentials_are_owner_only(tmp_path, oauth, existing):
    path = tmp_path / "oauth.json"
    previous = os.umask(0o022)
    try:
        if existing:
            path.write_text("previous")
        assert save(path) == str(path)
    finally:
        os.umask(previous)
    assert path.read_text() == oauth.to_json.return_value
    assert path.stat().st_mode & 0o777 == 0o600


def test_serialization_failure_preserves_existing_credentials(tmp_path, oauth):
    path = tmp_path / "oauth.json"
    path.write_text("previous")
    oauth.to_json.side_effect = ValueError("serialization failed")
    with pytest.raises(ValueError, match="serialization failed"):
        save(path)
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_replace_failure_preserves_old_file_and_removes_private_temp(
    tmp_path, oauth, monkeypatch
):
    path = tmp_path / "oauth.json"
    path.write_text("previous")

    def fail_replace(source, destination):
        temporary = Path(source)
        assert temporary.parent == path.parent
        assert temporary.read_text() == oauth.to_json.return_value
        if os.name == "posix":
            assert temporary.stat().st_mode & 0o777 == 0o600
        assert path.read_text() == "previous"
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        save(path)
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink fixture")
def test_destination_symlink_does_not_overwrite_its_target(tmp_path, oauth):
    other = tmp_path / "other.json"
    other.write_text("unrelated")
    path = tmp_path / "oauth.json"
    path.symlink_to(other)
    save(path)
    assert other.read_text() == "unrelated"
    assert not path.is_symlink()
    assert path.read_text() == oauth.to_json.return_value
