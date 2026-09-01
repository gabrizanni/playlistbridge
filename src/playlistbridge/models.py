"""Shared domain models used by import, matching, persistence, and UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MatchStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    REVIEW = "review"
    SKIPPED = "skipped"
    ADDED = "added"
    ERROR = "error"


@dataclass(slots=True)
class SourceTrack:
    position: int
    spotify_id: str | None = None
    title: str = ""
    artists: tuple[str, ...] = ()
    duration_seconds: int | None = None
    spotify_uri: str | None = None


@dataclass(slots=True)
class Candidate:
    video_id: str
    title: str
    artists: tuple[str, ...]
    duration_seconds: int | None
    score: float = 0.0


@dataclass(slots=True)
class TrackMatch:
    source: SourceTrack
    candidates: list[Candidate] = field(default_factory=list)
    selected_video_id: str | None = None
    confidence: float = 0.0
    status: MatchStatus = MatchStatus.PENDING
    error: str | None = None


@dataclass(slots=True)
class SourcePlaylist:
    name: str
    tracks: list[SourceTrack]
