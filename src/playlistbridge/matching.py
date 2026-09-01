"""Deterministic matching between Spotify tracks and YouTube Music results."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .models import Candidate, MatchStatus, SourceTrack, TrackMatch

MATCH_THRESHOLD = 0.86
REVIEW_THRESHOLD = 0.72

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FEAT_RE = re.compile(r"\b(?:feat(?:uring)?|ft)\.?\b.*$", re.IGNORECASE)
_QUALIFIERS: dict[str, tuple[re.Pattern[str], ...]] = {
    "live": (re.compile(r"\blive\b"),),
    "remix": (re.compile(r"\bremix(?:ed)?\b"), re.compile(r"\bmix\b")),
    "karaoke": (re.compile(r"\bkaraoke\b"),),
    "instrumental": (re.compile(r"\binstrumental\b"),),
    "sped_up": (
        re.compile(r"\bsped\s+up\b"),
        re.compile(r"\bspeed\s+up\b"),
        re.compile(r"\bnightcore\b"),
    ),
    "remaster": (re.compile(r"\bremaster(?:ed)?\b"),),
}


def normalize_text(value: str) -> str:
    """Return a comparison-friendly Unicode string.

    Accents, punctuation and compatibility variants are removed, while letters
    from non-Latin scripts remain valid alphanumeric characters.
    """

    value = unicodedata.normalize("NFKD", value or "").casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = "".join(char if char.isalnum() else " " for char in value)
    return " ".join(value.split())


def tokenize(value: str) -> set[str]:
    """Tokenize normalized text for order-independent comparisons."""

    normalized = normalize_text(value)
    # ``isalnum`` in normalize_text supports Unicode; the regex is a fast path
    # for Latin catalog metadata, with split() preserving other scripts.
    if normalized.isascii():
        return set(_TOKEN_RE.findall(normalized))
    return set(normalized.split())


def _similarity(left: str, right: str) -> float:
    """Dice similarity with a small exact-string bonus."""

    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    left_tokens = tokenize(left_normalized)
    right_tokens = tokenize(right_normalized)
    if not left_tokens or not right_tokens:
        return 0.0
    return 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))


def title_similarity(source_title: str, candidate_title: str) -> float:
    return _similarity(source_title, candidate_title)


def artist_similarity(source_artists: Iterable[str], candidate_artists: Iterable[str]) -> float:
    source = " ".join(_FEAT_RE.sub("", artist) for artist in source_artists)
    candidate = " ".join(_FEAT_RE.sub("", artist) for artist in candidate_artists)
    return _similarity(source, candidate)


def duration_similarity(source_seconds: int | None, candidate_seconds: int | None) -> float:
    """Score duration differences, tolerating small catalog rounding errors."""

    if source_seconds is None or candidate_seconds is None:
        return 0.5
    difference = abs(source_seconds - candidate_seconds)
    if difference <= 2:
        return 1.0
    if difference >= 30:
        return 0.0
    return max(0.0, 1.0 - (difference - 2) / 28)


def _qualifiers(title: str) -> set[str]:
    normalized = normalize_text(title)
    return {
        name
        for name, patterns in _QUALIFIERS.items()
        if any(pattern.search(normalized) for pattern in patterns)
    }


def qualifier_penalty(source_title: str, candidate_title: str) -> float:
    """Penalize version labels present on only one side of the comparison."""

    mismatches = _qualifiers(source_title) ^ _qualifiers(candidate_title)
    # One mismatch must be strong enough to keep an otherwise perfect but
    # semantically different version out of the automatic-match bucket.
    return min(0.45, 0.18 * len(mismatches))


def score_candidate(source: SourceTrack, candidate: Candidate) -> float:
    """Return a 0..1 confidence using title/artist/duration weights."""

    score = (
        0.55 * title_similarity(source.title, candidate.title)
        + 0.30 * artist_similarity(source.artists, candidate.artists)
        + 0.15 * duration_similarity(source.duration_seconds, candidate.duration_seconds)
        - qualifier_penalty(source.title, candidate.title)
    )
    return round(max(0.0, min(1.0, score)), 6)


def rank_candidates(source: SourceTrack, candidates: Iterable[Candidate]) -> list[Candidate]:
    """Score candidates in place and return them from best to worst."""

    ranked = list(candidates)
    for candidate in ranked:
        candidate.score = score_candidate(source, candidate)
    return sorted(ranked, key=lambda candidate: (-candidate.score, candidate.video_id))


def classify_match(source: SourceTrack, candidates: Iterable[Candidate]) -> TrackMatch:
    """Build a match result using the product's automatic/review thresholds."""

    ranked = rank_candidates(source, candidates)
    if not ranked:
        return TrackMatch(source=source, status=MatchStatus.REVIEW)

    confidence = ranked[0].score
    if confidence >= MATCH_THRESHOLD:
        return TrackMatch(
            source=source,
            candidates=ranked,
            selected_video_id=ranked[0].video_id,
            confidence=confidence,
            status=MatchStatus.MATCHED,
        )

    # Review candidates remain visible, but are deliberately not selected.
    return TrackMatch(
        source=source,
        candidates=ranked,
        selected_video_id=None,
        confidence=confidence,
        status=MatchStatus.REVIEW,
    )
