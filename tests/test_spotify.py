from __future__ import annotations

import json

import pytest

from playlistbridge.models import SourcePlaylist, SourceTrack
from playlistbridge.spotify import (
    SpotifyMetadataError,
    extract_spotify_track_ids,
    fetch_embed_metadata,
    hydrate_tracks,
    parse_clipboard,
)

TRACK_A = "4uLU6hMCjMI75M1A2tKUQC"
TRACK_B = "7qiZfU4dY1lWllzX7mPBI3"


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise SpotifyMetadataError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        for needle, response in self.routes.items():
            if needle in url:
                return FakeResponse(response)
        return FakeResponse("not found", 404)


def test_extract_ids_preserves_order_and_cross_row_duplicates() -> None:
    text = (
        f"spotify:track:{TRACK_A}\thttps://open.spotify.com/track/{TRACK_A}?si=x\n"
        f"https://open.spotify.com/intl-it/track/{TRACK_B}\n"
        f"spotify:track:{TRACK_A}\n"
    )

    assert extract_spotify_track_ids(text) == [TRACK_A, TRACK_B, TRACK_A]


def test_parse_clipboard_supports_ids_tabs_and_quoted_csv() -> None:
    text = (
        "Title\tArtist\tDuration\n"
        f"Song A\tArtist One, Artist Two\t3:05\tspotify:track:{TRACK_A}\n"
        '"Song, B","Artist Three",1:02:03\n'
        f"https://open.spotify.com/track/{TRACK_B}\n"
        f"spotify:track:{TRACK_A}\n"
    )

    playlist = parse_clipboard(text, "  Viaggio  ")

    assert playlist.name == "Viaggio"
    assert [track.position for track in playlist.tracks] == [1, 2, 3, 4]
    assert [track.spotify_id for track in playlist.tracks] == [TRACK_A, None, TRACK_B, TRACK_A]
    assert playlist.tracks[0].title == "Song A"
    assert playlist.tracks[0].artists == ("Artist One", "Artist Two")
    assert playlist.tracks[0].duration_seconds == 185
    assert playlist.tracks[1].title == "Song, B"
    assert playlist.tracks[1].artists == ("Artist Three",)
    assert playlist.tracks[1].duration_seconds == 3723


def test_parse_clipboard_discards_duplicate_token_only_within_row() -> None:
    text = (
        f"spotify:track:{TRACK_A}\thttps://open.spotify.com/track/{TRACK_A}\n"
        f"spotify:track:{TRACK_A}"
    )
    playlist = parse_clipboard(text, "Duplicates")
    assert [track.spotify_id for track in playlist.tracks] == [TRACK_A, TRACK_A]


def test_fetch_embed_metadata_reads_nested_initial_json() -> None:
    payload = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "id": TRACK_A,
                            "type": "track",
                            "name": "Test Song",
                            "artists": [{"name": "First"}, {"name": "Second"}],
                            "duration_ms": 185400,
                        }
                    }
                }
            }
        }
    }
    document = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></html>"
    )
    session = FakeSession({"/embed/track/": document})

    track = fetch_embed_metadata(TRACK_A, session=session)

    assert track.spotify_id == TRACK_A
    assert track.title == "Test Song"
    assert track.artists == ("First", "Second")
    assert track.duration_seconds == 185
    assert len(session.calls) == 1


def test_fetch_embed_metadata_falls_back_to_oembed() -> None:
    session = FakeSession(
        {
            "/embed/track/": "<html><body>No state</body></html>",
            "/oembed?": json.dumps({"title": "Fallback Song by Fallback Artist"}),
        }
    )

    track = fetch_embed_metadata(TRACK_B, session=session)

    assert track.title == "Fallback Song"
    assert track.artists == ("Fallback Artist",)
    assert track.duration_seconds is None
    assert len(session.calls) == 2


def test_embed_parser_does_not_use_metadata_for_a_different_track() -> None:
    wrong_payload = {
        "track": {
            "id": TRACK_A,
            "name": "Wrong Song",
            "artists": [{"name": "Wrong Artist"}],
        }
    }
    session = FakeSession(
        {
            "/embed/track/": f"<script>{json.dumps(wrong_payload)}</script>",
            "/oembed?": json.dumps({"title": "Correct Song by Correct Artist"}),
        }
    )

    track = fetch_embed_metadata(TRACK_B, session=session)

    assert track.title == "Correct Song"
    assert track.artists == ("Correct Artist",)


def test_hydrate_tracks_preserves_existing_values_and_positions() -> None:
    payload = {
        "track": {
            "id": TRACK_A,
            "name": "Remote Title",
            "artists": [{"name": "Remote Artist"}],
            "durationMs": 200_000,
        }
    }
    session = FakeSession({"/embed/track/": f"<script>{json.dumps(payload)}</script>"})
    original = SourcePlaylist(
        "List",
        [SourceTrack(position=7, spotify_id=TRACK_A, title="Clipboard Title")],
    )

    result = hydrate_tracks(original, session=session, strict=True)

    assert result is not original
    assert result.tracks[0].position == 7
    assert result.tracks[0].title == "Clipboard Title"
    assert result.tracks[0].artists == ("Remote Artist",)
    assert result.tracks[0].duration_seconds == 200


def test_invalid_track_id_is_rejected_without_network() -> None:
    with pytest.raises(ValueError):
        fetch_embed_metadata("not-an-id", session=FakeSession({}))
