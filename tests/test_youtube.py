import pytest

from playlistbridge.models import SourceTrack
from playlistbridge.youtube import YouTubeMusicAdapter, YouTubeMusicClient, YouTubeMusicError


class FakeClient:
    def __init__(self):
        self.search_calls = []
        self.created = []
        self.added = []
        self.failures = 0
        self.remote_ids = []

    def get_account_info(self):
        return {"accountName": "Test User"}

    def search(self, query, *, filter, limit):
        self.search_calls.append((query, filter, limit))
        return [
            {
                "videoId": "abc",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "duration": "3:05",
            },
            {"title": "Result without ID"},
        ]

    def create_playlist(self, title, description, privacy_status):
        self.created.append((title, description, privacy_status))
        return "playlist-id"

    def get_playlist(self, playlist_id, *, limit=None):
        return {"id": playlist_id, "tracks": [{"videoId": item} for item in self.remote_ids]}

    def add_playlist_items(self, playlist_id, video_ids, *, duplicates):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("secret-cookie-value")
        self.added.append((playlist_id, list(video_ids), duplicates))
        self.remote_ids.extend(video_ids)
        return {"status": "STATUS_SUCCEEDED"}


def test_auth_and_account_info_use_injected_client():
    adapter = YouTubeMusicAdapter(client=FakeClient())
    assert adapter.test_auth() is True
    assert adapter.get_account_info()["accountName"] == "Test User"


def test_search_maps_song_results_to_candidates():
    client = FakeClient()
    adapter = YouTubeMusicAdapter(client=client)
    track = SourceTrack(position=1, title="Song", artists=("Artist",))
    results = adapter.search_candidates(track, limit=5)
    assert client.search_calls == [("Artist Song", "songs", 5)]
    assert len(results) == 1
    assert results[0].video_id == "abc"
    assert results[0].artists == ("Artist",)
    assert results[0].duration_seconds == 185


def test_create_playlist_is_private():
    client = FakeClient()
    adapter = YouTubeMusicAdapter(client=client)
    assert adapter.create_private_playlist("Imported", "Description") == "playlist-id"
    assert client.created == [("Imported", "Description", "PRIVATE")]


def test_create_playlist_rejects_error_response():
    client = FakeClient()
    client.create_playlist = lambda *args: {"status": "FAILED"}
    with pytest.raises(YouTubeMusicError):
        YouTubeMusicAdapter(client=client).create_private_playlist("Imported")


def test_add_items_chunks_at_50_and_reports_progress():
    client = FakeClient()
    progress = []
    adapter = YouTubeMusicAdapter(client=client)
    count = adapter.add_playlist_items(
        "playlist-id",
        (f"id-{number}" for number in range(105)),
        progress=lambda done, total: progress.append((done, total)),
    )
    assert count == 105
    assert [len(call[1]) for call in client.added] == [50, 50, 5]
    assert all(call[2] is True for call in client.added)
    assert progress == [(0, 105), (50, 105), (100, 105), (105, 105)]


def test_retry_uses_exponential_backoff():
    client = FakeClient()
    client.failures = 2
    sleeps = []
    adapter = YouTubeMusicAdapter(
        client=client, retries=3, backoff_seconds=0.25, sleep=sleeps.append
    )
    assert adapter.add_playlist_items("playlist-id", ["abc"]) == 1
    assert sleeps == [0.25, 0.5]


def test_ambiguous_timeout_is_reconciled_without_duplicate_retry():
    client = FakeClient()
    calls = 0

    def commit_then_timeout(playlist_id, video_ids, *, duplicates):
        nonlocal calls
        calls += 1
        client.remote_ids.extend(video_ids)
        raise TimeoutError("response lost")

    client.add_playlist_items = commit_then_timeout
    adapter = YouTubeMusicAdapter(client=client, retries=3, sleep=lambda _: None)

    assert adapter.add_playlist_items("playlist-id", ["abc"], expected_existing=[]) == 1
    assert calls == 1
    assert client.remote_ids == ["abc"]


def test_destination_change_stops_before_writing():
    client = FakeClient()
    client.remote_ids = ["manual-item"]
    adapter = YouTubeMusicAdapter(client=client)
    with pytest.raises(YouTubeMusicError, match="è cambiata"):
        adapter.add_playlist_items("playlist-id", ["abc"], expected_existing=[])
    assert client.added == []


def test_errors_do_not_expose_underlying_cookie_or_token():
    client = FakeClient()
    client.failures = 1
    adapter = YouTubeMusicAdapter(client=client, retries=1)
    with pytest.raises(YouTubeMusicError) as error:
        adapter.add_playlist_items("playlist-id", ["abc"])
    assert "secret-cookie-value" not in str(error.value)


def test_failed_write_response_is_rejected():
    client = FakeClient()
    client.add_playlist_items = lambda *args, **kwargs: {"status": "FAILED"}
    with pytest.raises(YouTubeMusicError):
        YouTubeMusicAdapter(client=client, retries=1).add_playlist_items("playlist-id", ["abc"])


def test_invalid_retry_count_is_rejected():
    with pytest.raises(ValueError):
        YouTubeMusicAdapter(client=FakeClient(), retries=0)


def test_auth_dictionary_is_kept_in_memory_and_client_alias_is_available():
    auth = {"Cookie": "private-value", "X-Goog-AuthUser": "0"}
    adapter = YouTubeMusicClient(auth)
    assert isinstance(adapter, YouTubeMusicAdapter)
    assert adapter.auth == auth
    assert adapter.auth is not auth
