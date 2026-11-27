"""``seo-manager youtube update-description``: edit only a live video's description.

``videos.update(part="snippet")`` replaces the whole snippet, so a body that names only
the description would wipe the tags (and fail without a title and category). The edit
copies every writable snippet field it read, changes only the description, and reads
the video back: anything else that moved is an error, never a silent loss.
"""

from __future__ import annotations

import copy
import json

import pytest

from seo_manager import cli
from seo_manager.youtube import description_update as du

VID = "AAAAAAAAAAA"
SNIPPET = {
    "title": "Up on a ridge.",
    "description": "Up on a ridge. #Ireland\n\n#Shorts",
    "tags": ["Ireland", "Ridge"],
    "categoryId": "19",
    "defaultLanguage": "en",
    "defaultAudioLanguage": "en",
    "publishedAt": "2026-11-26T11:19:13Z",
    "channelId": "UCxxxx",
    "channelTitle": "Channel",
    "thumbnails": {"default": {"url": "https://i.ytimg.com/x.jpg"}},
    "liveBroadcastContent": "none",
    "localized": {"title": "Up on a ridge.", "description": "old"},
}
NEW = "Up on a ridge. #Ireland\n\nRead the full day: https://example.com/day/#day-10\n\n#Shorts"


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeYouTube:
    """Stands in for the Data API: ``update`` REPLACES the snippet like YouTube does."""

    def __init__(self, snippet=None, *, drop_on_update=()):
        self.store = {VID: copy.deepcopy(snippet or SNIPPET)}
        self.updates: list[dict] = []
        self.drop_on_update = drop_on_update

    def videos(self):
        return self

    def list(self, *, part, id):
        assert part == "snippet"
        return _Req(lambda: {"items": [{"id": id, "snippet": copy.deepcopy(self.store[id])}]
                             if id in self.store else []})  # fmt: skip

    def update(self, *, part, body):
        self.updates.append({"part": part, "body": copy.deepcopy(body)})

        def run():
            kept = {
                k: self.store[body["id"]][k]
                for k in du.READ_ONLY
                if k in self.store[body["id"]]
            }
            new = {**kept, **body["snippet"]}
            for k in self.drop_on_update:
                new.pop(k, None)
            self.store[body["id"]] = new
            return {"id": body["id"], "snippet": new}

        return _Req(run)


def test_only_the_description_changes_and_every_writable_field_is_sent():
    yt = FakeYouTube()
    out = du.update_description(yt, VID, NEW)
    [call] = yt.updates
    assert call["part"] == "snippet"
    sent = call["body"]["snippet"]
    assert sent["description"] == NEW
    for k in ("title", "tags", "categoryId", "defaultLanguage", "defaultAudioLanguage"):
        assert sent[k] == SNIPPET[k]
    assert not set(sent) & set(du.READ_ONLY)
    assert out["before"] == SNIPPET["description"] and out["after"] == NEW
    assert yt.store[VID]["tags"] == SNIPPET["tags"]


def test_a_stale_expected_description_is_refused_before_any_write():
    yt = FakeYouTube()
    with pytest.raises(du.DescriptionUpdateError, match="changed since"):
        du.update_description(yt, VID, NEW, expect_current="something else")
    assert yt.updates == []


def test_dry_run_reads_and_writes_nothing():
    yt = FakeYouTube()
    out = du.update_description(yt, VID, NEW, dry_run=True)
    assert yt.updates == [] and out["dry_run"] is True
    assert out["before"] == SNIPPET["description"] and out["after"] == NEW


@pytest.mark.parametrize("bad", ["a <b> c", "x" * 5001, ""])
def test_a_description_youtube_would_reject_is_refused_before_any_call(bad):
    yt = FakeYouTube()
    with pytest.raises(du.DescriptionUpdateError):
        du.update_description(yt, VID, bad)
    assert yt.updates == []


def test_a_field_lost_on_read_back_is_an_error_naming_it():
    yt = FakeYouTube(drop_on_update=("tags",))
    with pytest.raises(du.DescriptionUpdateError, match="tags"):
        du.update_description(yt, VID, NEW)


def test_an_unknown_video_is_an_error():
    with pytest.raises(du.DescriptionUpdateError, match="not found"):
        du.update_description(FakeYouTube(), "BBBBBBBBBBB", NEW)


def _run(argv, monkeypatch, yt, capsys):
    monkeypatch.setattr(cli, "_youtube_write_client", lambda path: yt)
    rc = cli.main(["youtube", *argv, "--youtube-credentials", "token.json"])
    return rc, capsys.readouterr()


def test_cli_show_prints_the_live_snippet(monkeypatch, capsys):
    rc, out = _run(["show", VID, "--json"], monkeypatch, FakeYouTube(), capsys)
    assert rc == 0
    assert json.loads(out.out)["description"] == SNIPPET["description"]


def test_cli_update_reads_back_and_prints_the_new_description(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "new.txt").write_text(NEW, encoding="utf-8")
    (tmp_path / "cur.txt").write_text(SNIPPET["description"], encoding="utf-8")
    yt = FakeYouTube()
    rc, out = _run(["update-description", VID, "--description-file", str(tmp_path / "new.txt"),
                    "--expect-current-file", str(tmp_path / "cur.txt"), "--json"],
                   monkeypatch, yt, capsys)  # fmt: skip
    assert rc == 0 and len(yt.updates) == 1
    assert json.loads(out.out)["after"] == NEW


def test_cli_dry_run_writes_nothing_and_a_refusal_exits_2(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "new.txt").write_text(NEW, encoding="utf-8")
    yt = FakeYouTube()
    rc, _ = _run(["update-description", VID, "--description-file", str(tmp_path / "new.txt"),
                  "--dry-run"], monkeypatch, yt, capsys)  # fmt: skip
    assert rc == 0 and yt.updates == []
    (tmp_path / "cur.txt").write_text("stale", encoding="utf-8")
    rc, out = _run(["update-description", VID, "--description-file", str(tmp_path / "new.txt"),
                    "--expect-current-file", str(tmp_path / "cur.txt")], monkeypatch, yt, capsys)  # fmt: skip
    assert rc == 2 and yt.updates == [] and "changed since" in out.err


def test_cli_without_credentials_exits_2(tmp_path, capsys):
    (tmp_path / "new.txt").write_text(NEW, encoding="utf-8")
    rc = cli.main(
        [
            "youtube",
            "update-description",
            VID,
            "--description-file",
            str(tmp_path / "new.txt"),
        ]
    )
    assert rc == 2 and "credentials" in capsys.readouterr().err


class LaggingYouTube(FakeYouTube):
    """YouTube serves the old snippet for a read or two after the write lands
    (e2-y6djJzZU, 2026-11-27: the update took, the next read still had the old text)."""

    def __init__(self, stale_reads, **kw):
        super().__init__(**kw)
        self.stale_reads = stale_reads
        self.old = None

    def update(self, *, part, body):
        self.old = copy.deepcopy(self.store[body["id"]])
        return super().update(part=part, body=body)

    def list(self, *, part, id):
        if self.old is not None and self.stale_reads > 0:
            self.stale_reads -= 1
            old = copy.deepcopy(self.old)
            return _Req(lambda: {"items": [{"id": id, "snippet": old}]})
        return super().list(part=part, id=id)


def test_a_read_back_that_lags_the_write_is_retried_until_it_lands():
    yt, slept = LaggingYouTube(stale_reads=2), []
    out = du.update_description(yt, VID, NEW, sleep=slept.append)
    assert out["after"] == NEW and len(yt.updates) == 1
    assert len(slept) == 2


def test_a_description_that_never_lands_is_still_an_error_after_the_retries():
    yt, slept = LaggingYouTube(stale_reads=99), []
    with pytest.raises(du.DescriptionUpdateError, match="did not land"):
        du.update_description(yt, VID, NEW, sleep=slept.append)
    assert len(slept) == du.READ_BACK_TRIES - 1
