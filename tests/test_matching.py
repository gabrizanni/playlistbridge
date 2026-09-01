from playlistbridge.matching import (
    classify_match,
    duration_similarity,
    normalize_text,
    score_candidate,
)
from playlistbridge.models import Candidate, MatchStatus, SourceTrack


def source(**changes):
    values = {
        "position": 1,
        "title": "Héroes",
        "artists": ("David Bowie",),
        "duration_seconds": 241,
    }
    values.update(changes)
    return SourceTrack(**values)


def candidate(**changes):
    values = {
        "video_id": "video-1",
        "title": "Heroes",
        "artists": ("David Bowie",),
        "duration_seconds": 242,
    }
    values.update(changes)
    return Candidate(**values)


def test_normalize_text_handles_unicode_and_punctuation():
    assert normalize_text("  HÉROES — Björk! ") == "heroes bjork"
    assert normalize_text("東京") == "東京"


def test_exact_metadata_is_automatic_match():
    result = classify_match(source(), [candidate()])
    assert result.status is MatchStatus.MATCHED
    assert result.selected_video_id == "video-1"
    assert result.confidence >= 0.86


def test_live_mismatch_prevents_automatic_selection():
    result = classify_match(source(), [candidate(title="Heroes (Live)")])
    assert result.status is MatchStatus.REVIEW
    assert result.selected_video_id is None


def test_all_version_mismatches_are_penalized():
    labels = ["Live", "Remix", "Karaoke", "Instrumental", "Sped Up", "Remastered"]
    baseline = score_candidate(source(), candidate())
    for label in labels:
        assert score_candidate(source(), candidate(title=f"Heroes ({label})")) < baseline


def test_low_score_and_empty_results_require_review_without_selection():
    poor = candidate(title="Unrelated Song", artists=("Someone Else",), duration_seconds=50)
    result = classify_match(source(), [poor])
    empty = classify_match(source(), [])
    assert result.status is MatchStatus.REVIEW
    assert result.selected_video_id is None
    assert empty.status is MatchStatus.REVIEW


def test_candidates_are_ranked_and_score_is_bounded():
    poor = candidate(video_id="poor", title="Something Else")
    good = candidate(video_id="good")
    result = classify_match(source(), [poor, good])
    assert [item.video_id for item in result.candidates] == ["good", "poor"]
    assert all(0 <= item.score <= 1 for item in result.candidates)


def test_duration_similarity_handles_missing_and_large_differences():
    assert duration_similarity(None, 200) == 0.5
    assert duration_similarity(200, 201) == 1.0
    assert duration_similarity(200, 250) == 0.0
