"""Durable local state and CSV reporting for resumable transfers."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Candidate, MatchStatus, SourcePlaylist, SourceTrack, TrackMatch

STATE_VERSION = 1


def default_data_dir() -> Path:
    """Return a per-user, non-roaming application data directory."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "PlaylistBridge"
    return Path.home() / ".playlistbridge"


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
    return value[:80] or "playlist"


def _safe_csv_text(value: str | None) -> str:
    """Prevent spreadsheet formula execution when a report is opened."""

    text = value or ""
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


@dataclass(slots=True)
class TransferSession:
    source: SourcePlaylist
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    matches: list[TrackMatch] = field(default_factory=list)
    destination_playlist_id: str | None = None
    destination_playlist_name: str | None = None
    transferred_positions: set[int] = field(default_factory=set)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def completed_count(self) -> int:
        return len(self.transferred_positions)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()


def _source_track_from_dict(data: dict[str, Any]) -> SourceTrack:
    return SourceTrack(
        position=int(data["position"]),
        spotify_id=data.get("spotify_id"),
        title=data.get("title", ""),
        artists=tuple(data.get("artists", ())),
        duration_seconds=data.get("duration_seconds"),
        spotify_uri=data.get("spotify_uri"),
    )


def _candidate_from_dict(data: dict[str, Any]) -> Candidate:
    return Candidate(
        video_id=data["video_id"],
        title=data.get("title", ""),
        artists=tuple(data.get("artists", ())),
        duration_seconds=data.get("duration_seconds"),
        score=float(data.get("score", 0.0)),
    )


def session_to_dict(session: TransferSession) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "session_id": session.session_id,
        "source": asdict(session.source),
        "matches": [
            {
                "source": asdict(match.source),
                "candidates": [asdict(candidate) for candidate in match.candidates],
                "selected_video_id": match.selected_video_id,
                "confidence": match.confidence,
                "status": match.status.value,
                "error": match.error,
            }
            for match in session.matches
        ],
        "destination_playlist_id": session.destination_playlist_id,
        "destination_playlist_name": session.destination_playlist_name,
        "transferred_positions": sorted(session.transferred_positions),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def session_from_dict(data: dict[str, Any]) -> TransferSession:
    if int(data.get("version", 0)) != STATE_VERSION:
        raise ValueError("Versione del file di sessione non supportata")
    source_data = data["source"]
    source = SourcePlaylist(
        name=source_data["name"],
        tracks=[_source_track_from_dict(item) for item in source_data.get("tracks", [])],
    )
    matches = []
    for item in data.get("matches", []):
        matches.append(
            TrackMatch(
                source=_source_track_from_dict(item["source"]),
                candidates=[_candidate_from_dict(c) for c in item.get("candidates", [])],
                selected_video_id=item.get("selected_video_id"),
                confidence=float(item.get("confidence", 0.0)),
                status=MatchStatus(item.get("status", MatchStatus.PENDING.value)),
                error=item.get("error"),
            )
        )
    return TransferSession(
        session_id=data["session_id"],
        source=source,
        matches=matches,
        destination_playlist_id=data.get("destination_playlist_id"),
        destination_playlist_name=data.get("destination_playlist_name"),
        transferred_positions=set(data.get("transferred_positions", [])),
        created_at=data.get("created_at", datetime.now(UTC).isoformat()),
        updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
    )


class SessionRepository:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_data_dir() / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, source: SourcePlaylist) -> TransferSession:
        session = TransferSession(source=source)
        self.save(session)
        return session

    def path_for(self, session: TransferSession) -> Path:
        return self.root / f"{_safe_filename(session.source.name)}-{session.session_id}.json"

    def save(self, session: TransferSession) -> Path:
        session.touch()
        destination = self.path_for(session)
        payload = json.dumps(session_to_dict(session), ensure_ascii=False, indent=2)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return destination

    def load(self, path: Path) -> TransferSession:
        return session_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_sessions(self) -> list[Path]:
        return sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def export_report(session: TransferSession, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    by_position = {match.source.position: match for match in session.matches}
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "playlist_sorgente",
                "playlist_destinazione",
                "youtube_playlist_id",
                "posizione",
                "titolo_spotify",
                "artisti_spotify",
                "spotify_uri",
                "stato",
                "confidenza",
                "youtube_video_id",
                "titolo_youtube",
                "artisti_youtube",
                "errore",
            ]
        )
        for track in session.source.tracks:
            match = by_position.get(track.position)
            candidate = None
            if match and match.selected_video_id:
                candidate = next(
                    (
                        item
                        for item in match.candidates
                        if item.video_id == match.selected_video_id
                    ),
                    None,
                )
            writer.writerow(
                [
                    _safe_csv_text(session.source.name),
                    _safe_csv_text(session.destination_playlist_name),
                    _safe_csv_text(session.destination_playlist_id),
                    track.position,
                    _safe_csv_text(track.title),
                    _safe_csv_text("; ".join(track.artists)),
                    _safe_csv_text(track.spotify_uri),
                    match.status.value if match else MatchStatus.PENDING.value,
                    f"{match.confidence:.3f}" if match else "",
                    _safe_csv_text(match.selected_video_id if match else None),
                    _safe_csv_text(candidate.title if candidate else None),
                    _safe_csv_text("; ".join(candidate.artists) if candidate else None),
                    _safe_csv_text(match.error if match else None),
                ]
            )
    return destination
