"""Small, testable adapter around the optional :mod:`ytmusicapi` package."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .models import Candidate, SourceTrack

ProgressCallback = Callable[[int, int], None]


class YouTubeMusicError(RuntimeError):
    """A safe public error that never exposes authentication material."""


class _YTMusicClient(Protocol):
    def get_account_info(self) -> Mapping[str, Any]: ...
    def search(self, query: str, *, filter: str, limit: int) -> Sequence[Mapping[str, Any]]: ...
    def create_playlist(self, title: str, description: str, privacy_status: str) -> str: ...
    def get_playlist(
        self, playlist_id: str, *, limit: int | None = None
    ) -> Mapping[str, Any]: ...
    def add_playlist_items(
        self, playlist_id: str, video_ids: Sequence[str], *, duplicates: bool
    ) -> Any: ...


def configure_browser_auth(auth_path: str | Path) -> Path:
    """Create a browser-auth file interactively using ytmusicapi.

    Importing this module remains safe when ytmusicapi is not installed; the
    dependency is required only when this function or the real adapter is used.
    """

    destination = Path(auth_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ytmusicapi import setup
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise YouTubeMusicError("ytmusicapi non è installato.") from exc
    try:
        setup(filepath=str(destination))
    except Exception as exc:
        raise YouTubeMusicError("Configurazione YouTube Music non riuscita.") from exc
    return destination


class YouTubeMusicAdapter:
    """YouTube Music operations used by the transfer workflow."""

    def __init__(
        self,
        auth_path: str | Path | Mapping[str, str] | None = None,
        *,
        client: _YTMusicClient | None = None,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 1:
            raise ValueError("retries deve essere almeno 1")
        if isinstance(auth_path, Mapping):
            # Keep decrypted credentials in memory only.  Do not stringify them:
            # that could accidentally surface header values in diagnostics.
            self.auth: str | dict[str, str] | None = dict(auth_path)
        elif auth_path:
            self.auth = str(Path(auth_path).expanduser().resolve())
        else:
            self.auth = None
        self._client = client
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    @property
    def client(self) -> _YTMusicClient:
        if self._client is None:
            if self.auth is None:
                raise YouTubeMusicError("Autenticazione YouTube Music non configurata.")
            try:
                from ytmusicapi import YTMusic
            except ImportError as exc:  # pragma: no cover - depends on installation
                raise YouTubeMusicError("ytmusicapi non è installato.") from exc
            try:
                self._client = YTMusic(self.auth)
            except Exception as exc:
                raise YouTubeMusicError("Accesso a YouTube Music non riuscito.") from exc
        return self._client

    def get_account_info(self) -> Mapping[str, Any]:
        try:
            return self.client.get_account_info()
        except Exception as exc:
            raise YouTubeMusicError("Impossibile leggere l'account YouTube Music.") from exc

    def test_auth(self) -> bool:
        """Validate credentials without returning or logging account secrets."""

        info = self.get_account_info()
        return bool(info)

    def search_candidates(self, track: SourceTrack, *, limit: int = 5) -> list[Candidate]:
        if limit < 1:
            return []
        query = " ".join((*track.artists, track.title)).strip()
        results: Sequence[Mapping[str, Any]] | None = None
        for attempt in range(self.retries):
            try:
                results = self.client.search(query, filter="songs", limit=limit)
                break
            except Exception as exc:
                if attempt + 1 >= self.retries:
                    raise YouTubeMusicError("Ricerca su YouTube Music non riuscita.") from exc
                self._sleep(self.backoff_seconds * (2**attempt))
        if results is None:  # pragma: no cover - loop always returns or raises
            return []

        candidates: list[Candidate] = []
        for item in results[:limit]:
            video_id = item.get("videoId")
            if not video_id:
                continue
            artists = tuple(
                str(artist.get("name", ""))
                for artist in item.get("artists", [])
                if isinstance(artist, Mapping) and artist.get("name")
            )
            candidates.append(
                Candidate(
                    video_id=str(video_id),
                    title=str(item.get("title", "")),
                    artists=artists,
                    duration_seconds=_duration_seconds(item),
                )
            )
        return candidates

    def create_private_playlist(self, title: str, description: str = "") -> str:
        try:
            playlist_id = self.client.create_playlist(title, description, "PRIVATE")
        except Exception as exc:
            raise YouTubeMusicError("Creazione della playlist non riuscita.") from exc
        if not isinstance(playlist_id, str) or not playlist_id:
            raise YouTubeMusicError("YouTube Music non ha restituito l'ID della playlist.")
        return playlist_id

    def get_playlist_video_ids(self, playlist_id: str) -> list[str]:
        """Return all destination IDs in their current remote order."""

        try:
            playlist = self.client.get_playlist(playlist_id, limit=None)
            tracks = playlist.get("tracks", [])
        except Exception as exc:
            raise YouTubeMusicError(
                "Impossibile verificare il contenuto della playlist di destinazione."
            ) from exc
        if not isinstance(tracks, Sequence) or isinstance(tracks, (str, bytes)):
            raise YouTubeMusicError(
                "YouTube Music ha restituito una playlist in un formato inatteso."
            )
        identifiers: list[str] = []
        for track in tracks:
            if not isinstance(track, Mapping) or not track.get("videoId"):
                raise YouTubeMusicError(
                    "La playlist contiene un elemento che non può essere verificato."
                )
            identifiers.append(str(track["videoId"]))
        return identifiers

    def add_playlist_items(
        self,
        playlist_id: str,
        video_ids: Iterable[str],
        *,
        progress: ProgressCallback | None = None,
        expected_existing: Sequence[str] | None = None,
    ) -> int:
        """Add videos in chunks, reconciling ambiguous writes before retrying."""

        identifiers = [identifier for identifier in video_ids if identifier]
        total = len(identifiers)
        completed = 0
        remote_before = self.get_playlist_video_ids(playlist_id)
        if expected_existing is not None and remote_before != list(expected_existing):
            raise YouTubeMusicError(
                "La playlist di destinazione è cambiata: trasferimento fermato "
                "per evitare duplicati."
            )
        if progress is not None:
            progress(completed, total)

        for offset in range(0, total, 50):
            chunk = identifiers[offset : offset + 50]
            self._retry_add(playlist_id, chunk, remote_before)
            remote_before.extend(chunk)
            completed += len(chunk)
            if progress is not None:
                progress(completed, total)
        return completed

    def _retry_add(
        self,
        playlist_id: str,
        video_ids: Sequence[str],
        expected_before: Sequence[str],
    ) -> None:
        expected_after = [*expected_before, *video_ids]
        for attempt in range(self.retries):
            try:
                response = self.client.add_playlist_items(playlist_id, video_ids, duplicates=True)
                if not _write_succeeded(response):
                    raise RuntimeError("YouTube Music ha rifiutato il blocco")
                return
            except Exception as exc:
                try:
                    remote = self.get_playlist_video_ids(playlist_id)
                except YouTubeMusicError as verification_error:
                    raise YouTubeMusicError(
                        "Esito dell'aggiunta incerto: impossibile verificare la playlist. "
                        "Trasferimento fermato per evitare duplicati."
                    ) from verification_error
                if remote == expected_after:
                    return
                if remote != list(expected_before):
                    raise YouTubeMusicError(
                        "La playlist di destinazione è cambiata durante il trasferimento. "
                        "Operazione fermata per evitare duplicati."
                    ) from exc
                if attempt + 1 >= self.retries:
                    raise YouTubeMusicError(
                        "Aggiunta dei brani non riuscita dopo più tentativi."
                    ) from exc
                self._sleep(self.backoff_seconds * (2**attempt))


def _write_succeeded(response: Any) -> bool:
    if isinstance(response, Mapping):
        return "SUCCEEDED" in str(response.get("status", "")).upper()
    if isinstance(response, str):
        return "SUCCEEDED" in response.upper()
    return False


def _duration_seconds(item: Mapping[str, Any]) -> int | None:
    raw_seconds = item.get("duration_seconds")
    if raw_seconds is not None:
        try:
            return int(raw_seconds)
        except (TypeError, ValueError):
            pass
    duration = item.get("duration")
    if not isinstance(duration, str):
        return None
    try:
        parts = [int(part) for part in duration.split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


# Friendly integration name; both names intentionally refer to the same API.
YouTubeMusicClient = YouTubeMusicAdapter
