import csv
from pathlib import Path

from playlistbridge.models import Candidate, MatchStatus, SourcePlaylist, SourceTrack, TrackMatch
from playlistbridge.storage import SessionRepository, export_report


def test_session_round_trip_preserves_duplicates_and_progress(tmp_path: Path) -> None:
    source = SourcePlaylist(
        name="Preferiti / estate",
        tracks=[
            SourceTrack(1, "abc", "Canzone", ("Artista",), 181, "spotify:track:abc"),
            SourceTrack(2, "abc", "Canzone", ("Artista",), 181, "spotify:track:abc"),
        ],
    )
    repository = SessionRepository(tmp_path)
    session = repository.create(source)
    session.matches = [
        TrackMatch(
            source=source.tracks[0],
            candidates=[Candidate("yt1", "Canzone", ("Artista",), 180, 0.99)],
            selected_video_id="yt1",
            confidence=0.99,
            status=MatchStatus.ADDED,
        )
    ]
    session.transferred_positions.add(1)
    path = repository.save(session)

    loaded = repository.load(path)

    assert [track.spotify_id for track in loaded.source.tracks] == ["abc", "abc"]
    assert loaded.matches[0].status is MatchStatus.ADDED
    assert loaded.transferred_positions == {1}


def test_export_report_is_excel_friendly(tmp_path: Path) -> None:
    track = SourceTrack(1, "abc", "È già", ("Beyoncé",), 180, "spotify:track:abc")
    session = SessionRepository(tmp_path / "state").create(SourcePlaylist("Test", [track]))
    destination = export_report(session, tmp_path / "report.csv")

    data = destination.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert "Beyonc" in data.decode("utf-8-sig")


def test_export_report_includes_destination_and_escapes_formulas(tmp_path: Path) -> None:
    track = SourceTrack(1, "abc", "=HYPERLINK(\"bad\")", ("+Artist",), 180)
    session = SessionRepository(tmp_path / "state").create(
        SourcePlaylist("@Source", [track])
    )
    session.destination_playlist_name = "-Destination"
    session.destination_playlist_id = "PL123"
    session.matches = [
        TrackMatch(
            source=track,
            candidates=[Candidate("yt1", "=YouTube", ("@YT",), 180, 0.9)],
            selected_video_id="yt1",
            confidence=0.9,
            status=MatchStatus.MATCHED,
        )
    ]

    destination = export_report(session, tmp_path / "report.csv")
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["playlist_sorgente"] == "'@Source"
    assert row["playlist_destinazione"] == "'-Destination"
    assert row["titolo_spotify"].startswith("'=")
    assert row["artisti_spotify"] == "'+Artist"
    assert row["titolo_youtube"] == "'=YouTube"
    assert row["artisti_youtube"] == "'@YT"
