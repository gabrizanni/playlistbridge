from pathlib import Path
from threading import Event

import pytest

from playlistbridge.models import Candidate, MatchStatus, SourcePlaylist, SourceTrack, TrackMatch
from playlistbridge.storage import SessionRepository
from playlistbridge.workflow import (
    WorkflowCancelled,
    default_destination_name,
    match_playlist,
    transfer_session,
)


class FakeYouTube:
    def __init__(self) -> None:
        self.created = 0
        self.added = []
        self.remote_ids = []

    def create_private_playlist(self, title, description=""):
        self.created += 1
        return "playlist-id"

    def get_playlist_video_ids(self, playlist_id):
        return list(self.remote_ids)

    def add_playlist_items(
        self, playlist_id, video_ids, *, progress=None, expected_existing=None
    ):
        assert self.remote_ids == list(expected_existing or [])
        values = list(video_ids)
        self.added.extend(values)
        if progress:
            progress(0, len(values))
        self.remote_ids.extend(values)
        if progress:
            progress(len(values), len(values))
        return len(values)


def test_transfer_is_resumable_and_does_not_recreate_playlist(tmp_path: Path) -> None:
    source = SourcePlaylist("Test", [SourceTrack(1, title="A", artists=("B",))])
    repository = SessionRepository(tmp_path)
    session = repository.create(source)
    session.matches = [
        TrackMatch(
            source=source.tracks[0],
            candidates=[Candidate("yt1", "A", ("B",), None, 1.0)],
            selected_video_id="yt1",
            confidence=1.0,
            status=MatchStatus.MATCHED,
        )
    ]
    youtube = FakeYouTube()

    assert transfer_session(session, youtube, repository) == 1
    assert transfer_session(session, youtube, repository) == 0
    assert youtube.created == 1
    assert youtube.added == ["yt1"]
    assert session.matches[0].status is MatchStatus.ADDED


def test_destination_name_is_distinct() -> None:
    assert default_destination_name("Preferiti").startswith("Preferiti (da Spotify ")


def test_destination_name_removes_unsafe_characters_and_is_bounded() -> None:
    name = default_destination_name('<Molto/Lungo:*?">' * 20)
    assert not any(character in name for character in '<>:"/\\|?*')
    assert len(name) <= 120


def test_resume_reconciles_remote_prefix_before_adding(tmp_path: Path) -> None:
    tracks = [
        SourceTrack(1, title="A", artists=("Artist",)),
        SourceTrack(2, title="B", artists=("Artist",)),
    ]
    repository = SessionRepository(tmp_path)
    session = repository.create(SourcePlaylist("Test", tracks))
    session.destination_playlist_id = "playlist-id"
    session.matches = [
        TrackMatch(source=track, selected_video_id=f"yt{index}", status=MatchStatus.MATCHED)
        for index, track in enumerate(tracks, start=1)
    ]
    youtube = FakeYouTube()
    youtube.remote_ids = ["yt1"]

    assert transfer_session(session, youtube, repository) == 1
    assert youtube.added == ["yt2"]
    assert session.transferred_positions == {1, 2}
    assert all(match.status is MatchStatus.ADDED for match in session.matches)


def test_cancel_after_remote_write_saves_checkpoint_first(tmp_path: Path) -> None:
    track = SourceTrack(1, title="A", artists=("Artist",))
    repository = SessionRepository(tmp_path)
    session = repository.create(SourcePlaylist("Test", [track]))
    session.matches = [
        TrackMatch(source=track, selected_video_id="yt1", status=MatchStatus.MATCHED)
    ]
    cancel = Event()

    class CancelAfterWrite(FakeYouTube):
        def add_playlist_items(
            self, playlist_id, video_ids, *, progress=None, expected_existing=None
        ):
            values = list(video_ids)
            if progress:
                progress(0, len(values))
            self.remote_ids.extend(values)
            cancel.set()
            if progress:
                progress(len(values), len(values))
            return len(values)

    with pytest.raises(WorkflowCancelled):
        transfer_session(session, CancelAfterWrite(), repository, cancel=cancel)

    assert session.transferred_positions == {1}
    assert session.matches[0].status is MatchStatus.ADDED
    saved = repository.load(repository.path_for(session))
    assert saved.transferred_positions == {1}


def test_matching_preserves_completed_manual_and_skipped_choices(tmp_path: Path) -> None:
    tracks = [
        SourceTrack(1, title="A", artists=("Artist",)),
        SourceTrack(2, title="B", artists=("Artist",)),
        SourceTrack(3, title="C", artists=("Artist",)),
    ]
    repository = SessionRepository(tmp_path)
    session = repository.create(SourcePlaylist("Test", tracks))
    session.matches = [
        TrackMatch(source=tracks[0], selected_video_id="done", status=MatchStatus.ADDED),
        TrackMatch(source=tracks[1], selected_video_id="manual", status=MatchStatus.MATCHED),
        TrackMatch(source=tracks[2], status=MatchStatus.SKIPPED),
    ]
    session.transferred_positions = {1}

    class NoSearch:
        def search_candidates(self, track, *, limit=5):
            raise AssertionError(f"unexpected search for {track.title}")

    result = match_playlist(session.source, NoSearch(), session=session, repository=repository)
    assert [match.status for match in result] == [
        MatchStatus.ADDED,
        MatchStatus.MATCHED,
        MatchStatus.SKIPPED,
    ]
    assert [match.selected_video_id for match in result] == ["done", "manual", None]
