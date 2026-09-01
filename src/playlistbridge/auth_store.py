"""Local handling of sensitive YouTube Music browser authentication data."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shlex
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .storage import default_data_dir

_MAGIC_DPAPI = b"PBD1"
_MAGIC_LOCAL = b"PBL1"


def normalize_header_dump(raw: str) -> str:
    """Convert raw headers or a Chrome/Edge cURL copy into header lines."""
    value = raw.strip()
    if not value:
        raise ValueError("Incolla gli header della richiesta YouTube Music")
    if not value.lower().startswith("curl "):
        return value

    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("Il comando cURL copiato non è completo") from exc

    headers: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-H", "--header"} and index + 1 < len(tokens):
            headers.append(tokens[index + 1])
            index += 2
            continue
        if token in {"-b", "--cookie"} and index + 1 < len(tokens):
            headers.append(f"cookie: {tokens[index + 1]}")
            index += 2
            continue
        index += 1
    if not headers:
        raise ValueError("Nessun header trovato nel comando cURL")
    return "\n".join(headers)


def build_browser_auth(raw: str) -> dict[str, Any]:
    """Use ytmusicapi's maintained parser to validate pasted browser headers."""
    try:
        from ytmusicapi import setup
    except ImportError as exc:
        raise RuntimeError("ytmusicapi non è installato") from exc

    normalized = normalize_header_dump(raw)
    try:
        result = setup(headers_raw=normalized)
    except Exception as exc:  # the dependency has several parser-specific exceptions
        raise ValueError("Header YouTube Music non validi o incompleti") from exc
    parsed = json.loads(result) if isinstance(result, str) else dict(result)
    lowered = {str(key).lower(): value for key, value in parsed.items()}
    if "cookie" not in lowered or not str(lowered["cookie"]).strip():
        raise ValueError("Negli header manca il cookie di YouTube Music")
    return parsed


if os.name == "nt":

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(data)
        return (
            _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
            buffer,
        )

    def _protect(data: bytes) -> bytes:
        source, source_buffer = _blob(data)
        destination = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source),
            "PlaylistBridge YouTube Music",
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(destination.pbData)
            del source_buffer

    def _unprotect(data: bytes) -> bytes:
        source, source_buffer = _blob(data)
        destination = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(destination.pbData)
            del source_buffer


class AuthStore:
    """Persist auth encrypted with DPAPI on Windows and permission-restricted elsewhere."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_dir() / "youtube_auth.bin"

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, auth: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(auth, ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            encoded = _MAGIC_DPAPI + _protect(payload)
        else:
            encoded = _MAGIC_LOCAL + base64.b64encode(payload)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def load(self) -> dict[str, Any]:
        encoded = self.path.read_bytes()
        if encoded.startswith(_MAGIC_DPAPI):
            if os.name != "nt":
                raise RuntimeError("Credenziali cifrate disponibili solo sul PC Windows originale")
            payload = _unprotect(encoded[len(_MAGIC_DPAPI) :])
        elif encoded.startswith(_MAGIC_LOCAL):
            payload = base64.b64decode(encoded[len(_MAGIC_LOCAL) :])
        else:
            raise ValueError("Formato delle credenziali non riconosciuto")
        return json.loads(payload.decode("utf-8"))

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()

    @staticmethod
    def redact(message: str) -> str:
        message = re.sub(r"(?i)(cookie|authorization)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", message)
        return message
