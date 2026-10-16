"""YouTubeUploader.upload_video refuses a title or text nobody reviewed.

The review is required, keyword-only and has no opt-out. It is checked on the
title in the request body, after payload validation and before any API call,
dry runs included.
"""

from unittest.mock import MagicMock

import pytest

from seo_manager.analytics.final_text_review import (
    FinalTextReviewRefused,
    review_to_dict,
)
from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.youtube import uploader as uploader_mod
from seo_manager.youtube.batch_uploader import (
    BatchUploadConfig,
    BatchUploader,
    VideoConfig,
)
from seo_manager.youtube.uploader import YouTubeUploader

from .final_text_fixture import (
    reviewed,
    sent_fields,
    sent_text,
    upload_kwargs,
)

TITLE = "Quillhaven Harbour at dawn"


def _metadata(title: str = TITLE, **overrides) -> SEOMetadata:
    fields = {"description": None, "tags": None, **sent_fields(TITLE), **overrides}
    return SEOMetadata(
        site_id="channel",
        content_id="video-1",
        platform=Platform.YOUTUBE,
        content_type=ContentType.VIDEO,
        title=title,
        description=fields["description"],
        tags=fields["tags"],
    )


@pytest.fixture
def up(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0")
    monkeypatch.setattr(uploader_mod, "MediaFileUpload", MagicMock())
    request = MagicMock()
    request.next_chunk.return_value = (None, {"id": "vid123"})
    uploader = YouTubeUploader.__new__(YouTubeUploader)
    uploader.youtube = MagicMock()
    uploader.youtube.videos.return_value.insert.return_value = request
    uploader.quota_used = 0
    return uploader, str(video)


def test_upload_without_a_review_is_a_type_error(up):
    uploader, video = up
    with pytest.raises(TypeError, match="final_text_review"):
        uploader.upload_video(video, _metadata())
    uploader.youtube.videos.assert_not_called()


def test_a_reviewed_upload_is_sent(up):
    uploader, video = up
    assert uploader.upload_video(video, _metadata(), **upload_kwargs(TITLE)) == "vid123"


def test_a_persisted_review_is_accepted(up):
    uploader, video = up
    review, context = reviewed(TITLE)
    assert (
        uploader.upload_video(
            video,
            _metadata(),
            final_text_review=review_to_dict(review),
            final_text_context=context,
        )
        == "vid123"
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_title_changed_after_review_is_refused_before_any_call(up, dry_run):
    uploader, video = up
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video,
            _metadata(TITLE + " in 4K"),
            dry_run=dry_run,
            **upload_kwargs(TITLE),
        )
    assert caught.value.code == "stale:title"
    uploader.youtube.videos.assert_not_called()


@pytest.mark.parametrize("marker", [None, {}])
def test_an_empty_review_never_counts(up, marker):
    uploader, video = up
    _, context = reviewed(TITLE)
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video, _metadata(), final_text_review=marker, final_text_context=context
        )
    assert caught.value.code == "review_missing"
    uploader.youtube.videos.assert_not_called()


def test_changed_on_screen_text_is_refused(up):
    uploader, video = up
    review, context = reviewed(TITLE)
    context = {**context, "visible": {"ost": "Quillhaven"}}
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video, _metadata(), final_text_review=review, final_text_context=context
        )
    assert caught.value.code == "stale:text:ost"


def test_a_changed_source_is_refused(up):
    uploader, video = up
    review, context = reviewed(TITLE)
    context = {**context, "source_fingerprint": "src-other"}
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video, _metadata(), final_text_review=review, final_text_context=context
        )
    assert caught.value.code == "stale:source"


def test_a_context_missing_a_field_is_refused(up):
    uploader, video = up
    review, context = reviewed(TITLE)
    del context["plan_fingerprint"]
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video, _metadata(), final_text_review=review, final_text_context=context
        )
    assert caught.value.code == "stale:plan"


def test_batch_upload_carries_each_videos_review(monkeypatch, up):
    uploader, video = up
    review, context = reviewed(TITLE)
    batch = BatchUploader(credentials_path="fake_creds.json", project_id="p")
    monkeypatch.setattr(batch, "_get_uploader", lambda: uploader)
    good = VideoConfig(
        video_path=video,
        metadata=_metadata(),
        final_text_review=review,
        final_text_context=context,
    )
    unreviewed = VideoConfig(video_path=video, metadata=_metadata())
    result = batch.upload_batch(
        BatchUploadConfig(videos=[good, unreviewed], continue_on_error=True)
    )
    assert [r.success for r in result.results] == [True, False]
    assert "review_missing" in result.results[1].error
    assert uploader.youtube.videos.return_value.insert.call_count == 1


def test_the_composed_description_and_tags_are_sent(up):
    uploader, video = up
    description, tags = sent_text(reviewed(TITLE)[0])
    assert description == TITLE + "\nSubscribe for more drives."
    assert tags == ["scenic drive"]
    assert uploader.upload_video(video, _metadata(), **upload_kwargs(TITLE)) == "vid123"
    body = uploader.youtube.videos.return_value.insert.call_args.kwargs["body"]
    assert body["snippet"]["description"] == description
    assert body["snippet"]["tags"] == tags


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "edit",
    [
        TITLE + "\nBuy my course!",
        "Subscribe for more drives.\n" + TITLE,
        "",
    ],
)
def test_a_description_edited_after_review_is_refused_before_any_call(
    up, dry_run, edit
):
    uploader, video = up
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video,
            _metadata(description=edit),
            dry_run=dry_run,
            **upload_kwargs(TITLE),
        )
    assert caught.value.code == "stale:description"
    uploader.youtube.videos.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("edit", [["scenic drive", "cheap pills"], [], ["road trip"]])
def test_tags_edited_after_review_are_refused_before_any_call(up, dry_run, edit):
    uploader, video = up
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video, _metadata(tags=edit), dry_run=dry_run, **upload_kwargs(TITLE)
        )
    assert caught.value.code == "stale:tags"
    uploader.youtube.videos.assert_not_called()


def test_every_stale_reason_is_named_together(up):
    uploader, video = up
    with pytest.raises(FinalTextReviewRefused) as caught:
        uploader.upload_video(
            video,
            _metadata(description="x", tags=["y"]),
            **upload_kwargs(TITLE),
        )
    assert caught.value.reasons == ("stale:description", "stale:tags")


def test_an_edited_chapter_line_is_refused(up):
    uploader, video = up
    review, context = reviewed(TITLE, chapters=True)
    description, tags = sent_text(review)
    kwargs = {"final_text_review": review, "final_text_context": context}
    assert description.splitlines()[1:3] == [f"0:00 {TITLE}", f"2:00 {TITLE}"]
    # Unedited: sent.
    assert (
        uploader.upload_video(
            video, _metadata(description=description, tags=tags), **kwargs
        )
        == "vid123"
    )
    lines = description.splitlines()
    for edited in (
        "\n".join([lines[0], lines[2], lines[1], *lines[3:]]),
        description.replace(f"2:00 {TITLE}", "2:00 Elsewhere"),
    ):
        with pytest.raises(FinalTextReviewRefused) as caught:
            uploader.upload_video(
                video, _metadata(description=edited, tags=tags), **kwargs
            )
        assert caught.value.code == "stale:description"
