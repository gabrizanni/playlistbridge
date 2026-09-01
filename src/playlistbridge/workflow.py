"""High-level, UI-independent playlist transfer workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event

from .matching import classify_match
from .models import MatchStatus, SourcePlaylist, TrackMatch
from .spotify import SpotifyMetadataError, fetch_embed_metadata
from .storage import SessionRepository, TransferSession, export_report
from .youtube import YouTubeMusicAdapter

Progress = Callable[[int, int, str], None]


class WorkflowCancelled(RuntimeError):
    pass


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise WorkflowCancelled("Operazione annullata")


def hydrate_playlist(
    playlist: SourcePlaylist,
    *,
    repository: SessionRepository | None = None,
    session: TransferSession | None = None,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> SourcePlaylist:
    """Fill missing Spotify metadata one track at a time, preserving failures."""
    hydrated = []
    total = len(playlist.tracks)
    for index, track in enumerate(playlist.tracks, start=1):
        _check_cancel(cancel)
        updated = replace(track)
        if track.spotify_id and (
            not track.title or not track.artists or track.duration_seconds is None
        ):
            try:
                remote = fetch_embed_metadata(track.spotify_id)
            except (SpotifyMetadataError, OSError, ValueError):
                pass
            else:
                updated = replace(
                    track,
                    title=track.title or remote.title,
                    artists=track.artists or remote.artists,
                    duration_seconds=(
                        track.duration_seconds
                        if track.duration_seconds is not None
                        else remote.duration_seconds
                    ),
                    spotify_uri=track.spotify_uri or remote.spotify_uri,
                )
        hydrated.append(updated)
        if session is not None:
            session.source = SourcePlaylist(playlist.name, list(hydrated) + playlist.tracks[index:])
            if repository is not None and (index % 10 == 0 or index == total):
                repository.save(session)
        if progress:
            progress(index, total, "Recupero metadati Spotify")
    return SourcePlaylist(playlist.name, hydrated)


def match_playlist(
    playlist: SourcePlaylist,
    youtube: YouTubeMusicAdapter,
    *,
    repository: SessionRepository | None = None,
    session: TransferSession | None = None,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> list[TrackMatch]:
    matches: list[TrackMatch] = []
    existing_by_position = (
        {match.source.position: match for match in session.matches} if session else {}
    )
    total = len(playlist.tracks)
    for index, track in enumerate(playlist.tracks, start=1):
        _check_cancel(cancel)
        existing = existing_by_position.get(track.position)
        if existing is not None and (
            track.position in (session.transferred_positions if session else set())
            or existing.status is MatchStatus.ADDED
        ):
            existing.source = track
            existing.status = MatchStatus.ADDED
            match = existing
        elif existing is not None and existing.status is MatchStatus.SKIPPED:
            existing.source = track
            match = existing
        elif (
            existing is not None
            and existing.selected_video_id
            and existing.status is MatchStatus.MATCHED
        ):
            existing.source = track
            match = existing
        elif not track.title or not track.artists:
            match = TrackMatch(
                source=track,
                status=MatchStatus.REVIEW,
                error="Metadati Spotify insufficienti",
            )
        else:
            try:
                match = classify_match(track, youtube.search_candidates(track, limit=5))
            except Exception as exc:
                match = TrackMatch(
                    source=track,
                    status=MatchStatus.ERROR,
                    error=str(exc),
                )
        matches.append(match)
        if session is not None:
            session.matches = list(matches)
            session.source = playlist
            if repository is not None and (index % 10 == 0 or index == total):
                repository.save(session)
        if progress:
            progress(index, total, "Ricerca su YouTube Music")
    return matches


def default_destination_name(source_name: str) -> str:
    clean_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", source_name)
    clean_name = " ".join(clean_name.split()).strip(". ") or "Playlist Spotify"
    suffix = f" (da Spotify {datetime.now().date().isoformat()})"
    return f"{clean_name[: 120 - len(suffix)]}{suffix}"


def transfer_session(
    session: TransferSession,
    youtube: YouTubeMusicAdapter,
    repository: SessionRepository,
    *,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> int:
    """Create once and reconcile remote state before every resumable addition."""
    _check_cancel(cancel)
    planned: list[TrackMatch] = [
        match
        for match in session.matches
        if match.selected_video_id
        and match.status in {MatchStatus.MATCHED, MatchStatus.ERROR, MatchStatus.ADDED}
    ]
    if not planned:
        return 0

    remote_ids: list[str] = []
    if session.destination_playlist_id is not None:
        remote_ids = youtube.get_playlist_video_ids(session.destination_playlist_id)
        expected_ids = [match.selected_video_id for match in planned]
        if len(remote_ids) > len(expected_ids) or remote_ids != expected_ids[: len(remote_ids)]:
            raise RuntimeError(
                "La playlist YouTube Music è stata modificata fuori da PlaylistBridge. "
                "Trasferimento fermato per evitare duplicati o sovrascritture."
            )

        planned_positions = {match.source.position for match in planned}
        session.transferred_positions.difference_update(planned_positions)
        for index, match in enumerate(planned):
            if index < len(remote_ids):
                match.status = MatchStatus.ADDED
                session.transferred_positions.add(match.source.position)
            elif match.status is MatchStatus.ADDED:
                match.status = MatchStatus.MATCHED
        repository.save(session)

    pending = planned[len(remote_ids) :]
    if not pending:
        return 0

    if session.destination_playlist_id is None:
        destination_name = session.destination_playlist_name or default_destination_name(
            session.source.name
        )
        session.destination_playlist_id = youtube.create_private_playlist(
            destination_name,
            "Trasferita localmente con PlaylistBridge",
        )
        session.destination_playlist_name = destination_name
        repository.save(session)

    video_ids = [match.selected_video_id for match in pending if match.selected_video_id]

    def checkpoint(done: int, total: int) -> None:
        for match in pending[:done]:
            match.status = MatchStatus.ADDED
            session.transferred_positions.add(match.source.position)
        repository.save(session)
        if progress:
            progress(done, total, "Aggiunta alla playlist YouTube Music")
        _check_cancel(cancel)

    return youtube.add_playlist_items(
        session.destination_playlist_id,
        video_ids,
        progress=checkpoint,
        expected_existing=remote_ids,
    )


def write_session_report(session: TransferSession, directory: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_id = session.session_id[:8]
    return export_report(session, directory / f"report-{safe_id}-{timestamp}.csv")
