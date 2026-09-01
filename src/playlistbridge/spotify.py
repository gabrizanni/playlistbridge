"""Import Spotify track data without using Spotify's authenticated Web API.

Clipboard parsing is deliberately independent from metadata hydration: a paste is
useful even when Spotify is unreachable, and public embed pages can be queried at
a later time for tracks that only have an identifier.
"""

from __future__ import annotations

import csv
import html
import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from html.parser import HTMLParser
from io import StringIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from .models import SourcePlaylist, SourceTrack

_TRACK_ID = r"[A-Za-z0-9]{22}"
_TRACK_TOKEN_RE = re.compile(
    rf"(?P<uri>spotify:track:(?P<uri_id>{_TRACK_ID}))"
    rf"|(?P<url>https?://open\.spotify\.com/(?:intl-[a-z]{{2}}/)?track/"
    rf"(?P<url_id>{_TRACK_ID})(?:[/?#][^\s,;\t]*)?)",
    re.IGNORECASE,
)
_BARE_TRACK_ID_RE = re.compile(rf"^{_TRACK_ID}$")
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_HEADER_WORDS = {
    "title",
    "track",
    "track title",
    "song",
    "name",
    "titolo",
    "brano",
}
_ARTIST_HEADERS = {"artist", "artists", "artista", "artisti"}
_DURATION_HEADERS = {"duration", "length", "durata"}

EMBED_URL = "https://open.spotify.com/embed/track/{track_id}"
OEMBED_URL = "https://open.spotify.com/oembed?url={track_url}"
USER_AGENT = "PlaylistBridge/0.1 (+local personal playlist migration)"


class SpotifyMetadataError(RuntimeError):
    """Raised when public Spotify metadata cannot be downloaded or parsed."""


def extract_spotify_track_ids(text: str) -> list[str]:
    """Extract Spotify track IDs, preserving order and meaningful duplicates.

    The same ID is emitted at most once per input line.  This handles clipboard
    formats that include both a URI and URL for one row, while retaining the same
    song when it occurs again on a later row in the playlist.
    """

    result: list[str] = []
    for line in text.splitlines():
        seen_on_line: set[str] = set()
        for match in _TRACK_TOKEN_RE.finditer(line):
            track_id = match.group("uri_id") or match.group("url_id")
            if track_id not in seen_on_line:
                result.append(track_id)
                seen_on_line.add(track_id)
    return result


def parse_clipboard(text: str, playlist_name: str) -> SourcePlaylist:
    """Parse Spotify URIs/URLs and common tab- or comma-separated rows.

    Supported metadata rows use the conventional ``title, artist, duration``
    order (extra middle columns are ignored).  A row containing several distinct
    Spotify IDs produces one track per ID; metadata on that row applies to the
    first one only because there is no reliable way to associate it to the rest.
    """

    tracks: list[SourceTrack] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        ids = _ids_from_line(line)
        fields = _split_metadata_row(line)
        metadata = _metadata_from_fields(fields, ids)

        if ids:
            for index, track_id in enumerate(ids):
                title, artists, duration = metadata if index == 0 else ("", (), None)
                tracks.append(
                    SourceTrack(
                        position=len(tracks) + 1,
                        spotify_id=track_id,
                        title=title,
                        artists=artists,
                        duration_seconds=duration,
                        spotify_uri=f"spotify:track:{track_id}",
                    )
                )
        elif metadata != ("", (), None) and not _looks_like_header(fields):
            title, artists, duration = metadata
            if title and artists:
                tracks.append(
                    SourceTrack(
                        position=len(tracks) + 1,
                        title=title,
                        artists=artists,
                        duration_seconds=duration,
                    )
                )

    return SourcePlaylist(name=playlist_name.strip() or "Playlist Spotify", tracks=tracks)


def fetch_embed_metadata(track_id: str, session: Any = None) -> SourceTrack:
    """Fetch one track from Spotify's public embed page.

    ``session`` may be a requests-like object with ``get``; leaving it unset uses
    the Python standard library.  Up to three attempts are made for transient
    failures.  Spotify oEmbed is used as a fallback when embed JSON is unavailable.
    """

    if not _BARE_TRACK_ID_RE.fullmatch(track_id):
        raise ValueError(f"Invalid Spotify track ID: {track_id!r}")

    embed_error: Exception | None = None
    try:
        embed_text = _get_text(EMBED_URL.format(track_id=track_id), session=session)
        metadata = _parse_embed_html(embed_text, track_id)
        if metadata is not None:
            return metadata
    except (SpotifyMetadataError, HTTPError, URLError, OSError, ValueError) as exc:
        embed_error = exc

    track_url = f"https://open.spotify.com/track/{track_id}"
    try:
        payload = _get_json(OEMBED_URL.format(track_url=quote(track_url, safe="")), session=session)
        title, artists = _parse_oembed_title(str(payload.get("title", "")))
        if title:
            return SourceTrack(
                position=0,
                spotify_id=track_id,
                spotify_uri=f"spotify:track:{track_id}",
                title=title,
                artists=artists,
            )
    except (
        SpotifyMetadataError,
        HTTPError,
        URLError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        if embed_error is None:
            embed_error = exc

    detail = f": {embed_error}" if embed_error else ""
    raise SpotifyMetadataError(f"No public metadata found for {track_id}{detail}")


def hydrate_tracks(
    playlist: SourcePlaylist, session: Any = None, *, strict: bool = False
) -> SourcePlaylist:
    """Return a copy of ``playlist`` with missing metadata filled from Spotify.

    Existing clipboard metadata wins.  In the default best-effort mode a failed
    lookup leaves that track untouched; ``strict=True`` exposes the first error.
    """

    hydrated: list[SourceTrack] = []
    for track in playlist.tracks:
        needs_metadata = not track.title or not track.artists or track.duration_seconds is None
        if not track.spotify_id or not needs_metadata:
            hydrated.append(replace(track))
            continue
        try:
            remote = fetch_embed_metadata(track.spotify_id, session=session)
        except (SpotifyMetadataError, HTTPError, URLError, OSError, ValueError):
            if strict:
                raise
            hydrated.append(replace(track))
            continue
        hydrated.append(
            replace(
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
        )
    return SourcePlaylist(name=playlist.name, tracks=hydrated)


def _ids_from_line(line: str) -> list[str]:
    ids = extract_spotify_track_ids(line)
    if ids:
        return ids
    # Accept an ID by itself, but not arbitrary 22-character fields in a CSV row.
    return [line] if _BARE_TRACK_ID_RE.fullmatch(line) else []


def _split_metadata_row(line: str) -> list[str]:
    delimiter = "\t" if "\t" in line else "," if "," in line else None
    if delimiter is None:
        return [line.strip()]
    try:
        return [cell.strip() for cell in next(csv.reader(StringIO(line), delimiter=delimiter))]
    except (csv.Error, StopIteration):
        return [part.strip() for part in line.split(delimiter)]


def _metadata_from_fields(
    fields: list[str], track_ids: list[str]
) -> tuple[str, tuple[str, ...], int | None]:
    clean: list[str] = []
    for field in fields:
        without_tokens = _TRACK_TOKEN_RE.sub("", field).strip(" -|;()[]")
        if without_tokens and not _BARE_TRACK_ID_RE.fullmatch(without_tokens):
            clean.append(without_tokens)

    if len(clean) < 2:
        return "", (), None

    duration: int | None = None
    duration_index: int | None = None
    for index in range(len(clean) - 1, -1, -1):
        parsed = _parse_duration(clean[index])
        if parsed is not None:
            duration = parsed
            duration_index = index
            break

    values = [value for index, value in enumerate(clean) if index != duration_index]
    if len(values) < 2:
        return "", (), duration
    title = values[0].strip()
    artists = _parse_artists(values[1])
    return title, artists, duration


def _parse_artists(value: str) -> tuple[str, ...]:
    artists = tuple(part.strip() for part in re.split(r"\s*[;,]\s*", value) if part.strip())
    return artists or ((value.strip(),) if value.strip() else ())


def _parse_duration(value: str) -> int | None:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    if seconds >= 60 or (hours and minutes >= 60):
        return None
    return hours * 3600 + minutes * 60 + seconds


def _looks_like_header(fields: Iterable[str]) -> bool:
    lowered = {field.strip().casefold() for field in fields}
    return bool(lowered & _HEADER_WORDS and lowered & _ARTIST_HEADERS) or bool(
        lowered & _HEADER_WORDS and lowered & _DURATION_HEADERS
    )


def _get_text(url: str, session: Any = None) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if session is None:
                try:
                    import requests
                except ImportError as exc:
                    raise SpotifyMetadataError("requests non è installato") from exc
                response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
                response.raise_for_status()
                return response.text
            response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            status = getattr(response, "status_code", 200)
            if status >= 400:
                raise SpotifyMetadataError(f"HTTP {status} for {url}")
            value = response.text
            return value() if callable(value) else str(value)
        # A caller may inject urllib, requests, httpx, or a small test double;
        # normalize their different transport exception types at this boundary.
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (2**attempt))
    raise SpotifyMetadataError(f"Unable to fetch {url}: {last_error}")


def _get_json(url: str, session: Any = None) -> Mapping[str, Any]:
    text = _get_text(url, session=session)
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise SpotifyMetadataError("Spotify returned a non-object JSON response")
    return payload


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "script":
            self._in_script = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._in_script:
            self.scripts.append("".join(self._parts).strip())
            self._in_script = False
            self._parts = []


def _parse_embed_html(document: str, track_id: str) -> SourceTrack | None:
    collector = _ScriptCollector()
    collector.feed(document)
    for script in collector.scripts:
        if not script or script[0] not in "[{":
            continue
        try:
            payload = json.loads(html.unescape(script))
        except json.JSONDecodeError:
            continue
        candidate = _find_track_mapping(payload, track_id)
        if candidate is not None:
            parsed = _track_from_mapping(candidate, track_id)
            if parsed.title:
                return parsed
    return None


def _find_track_mapping(value: Any, track_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if _mapping_looks_like_track(value):
            identifier = str(value.get("id") or value.get("uri") or "")
            if track_id in identifier:
                return value
        for child in value.values():
            found = _find_track_mapping(child, track_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_track_mapping(child, track_id)
            if found is not None:
                return found
    return None


def _mapping_looks_like_track(value: Mapping[str, Any]) -> bool:
    return bool(value.get("name") or value.get("title")) and bool(
        value.get("artists") or value.get("artist") or value.get("subtitle")
    )


def _track_from_mapping(value: Mapping[str, Any], track_id: str) -> SourceTrack:
    title = str(value.get("name") or value.get("title") or "").strip()
    artists = _artists_from_json(value.get("artists") or value.get("artist"))
    if not artists and value.get("subtitle"):
        artists = _parse_artists(str(value["subtitle"]))

    raw_duration = (
        value.get("duration_ms")
        or value.get("durationMs")
        or value.get("duration_milliseconds")
        or value.get("duration")
        or value.get("duration_seconds")
    )
    duration = _duration_from_json(raw_duration)
    return SourceTrack(
        position=0,
        spotify_id=track_id,
        spotify_uri=f"spotify:track:{track_id}",
        title=title,
        artists=artists,
        duration_seconds=duration,
    )


def _artists_from_json(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return _parse_artists(value)
    if isinstance(value, Mapping):
        name = value.get("name") or value.get("title")
        return (str(name).strip(),) if name else ()
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("title")
            else:
                name = item
            if name and str(name).strip():
                names.append(str(name).strip())
        return tuple(names)
    return ()


def _duration_from_json(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = _parse_duration(value)
        if parsed is not None:
            return parsed
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    # Spotify's embed state normally uses milliseconds; small values are seconds.
    return round(numeric / 1000) if numeric >= 10_000 else round(numeric)


def _parse_oembed_title(value: str) -> tuple[str, tuple[str, ...]]:
    title = html.unescape(value).strip()
    match = re.match(r"^(.*?)\s+by\s+(.+)$", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(), _parse_artists(match.group(2))
    return title, ()
