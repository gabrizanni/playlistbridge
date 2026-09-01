from pathlib import Path

import pytest

from playlistbridge.auth_store import AuthStore, normalize_header_dump


def test_normalize_curl_extracts_headers_and_cookie() -> None:
    raw = "curl 'https://music.youtube.com/youtubei/v1/browse' -H 'accept: */*' -b 'SAPISID=secret'"
    normalized = normalize_header_dump(raw)
    assert "accept: */*" in normalized
    assert "cookie: SAPISID=secret" in normalized


def test_auth_round_trip(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.bin")
    store.save({"Cookie": "SAPISID=secret", "User-Agent": "test"})
    assert store.load()["Cookie"] == "SAPISID=secret"
    store.delete()
    assert not store.exists()


def test_empty_header_dump_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_header_dump("  ")
