"""Minimal ClamAV INSTREAM client; never invokes a shell."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class AntivirusVerdict(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AntivirusResult:
    verdict: AntivirusVerdict
    detail: str


class AntivirusScanner(Protocol):
    def scan(self, path: Path) -> AntivirusResult: ...


class ClamAVScanner:
    def __init__(self, endpoint: str | None, *, timeout_seconds: float = 15.0) -> None:
        self._endpoint = endpoint.strip() if endpoint else ""
        self._timeout = timeout_seconds

    def scan(self, path: Path) -> AntivirusResult:
        if not self._endpoint:
            return AntivirusResult(
                AntivirusVerdict.UNAVAILABLE, "ClamAV endpoint is not configured"
            )
        try:
            connection = self._connect()
            with connection:
                connection.sendall(b"zINSTREAM\0")
                with path.open("rb") as handle:
                    while chunk := handle.read(64 * 1024):
                        connection.sendall(struct.pack("!I", len(chunk)))
                        connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = self._receive_response(connection)
        except (OSError, TimeoutError) as exc:
            return AntivirusResult(AntivirusVerdict.UNAVAILABLE, type(exc).__name__)
        upper = response.upper()
        if upper.endswith(" OK") or upper.endswith(" OK\x00"):
            return AntivirusResult(AntivirusVerdict.CLEAN, "ClamAV scan passed")
        if " FOUND" in upper:
            return AntivirusResult(AntivirusVerdict.INFECTED, "malware detected")
        return AntivirusResult(AntivirusVerdict.ERROR, "unrecognized ClamAV response")

    def _connect(self) -> socket.socket:
        if self._endpoint.startswith("/"):
            unix_family = getattr(socket, "AF_UNIX", None)
            if unix_family is None:
                raise OSError("Unix sockets are not supported on this platform")
            connection = socket.socket(unix_family, socket.SOCK_STREAM)
            connection.settimeout(self._timeout)
            connection.connect(self._endpoint)
            return connection
        host, separator, raw_port = self._endpoint.rpartition(":")
        if not separator or not host or not raw_port.isdigit():
            raise OSError("invalid ClamAV endpoint")
        return socket.create_connection((host, int(raw_port)), timeout=self._timeout)

    @staticmethod
    def _receive_response(connection: socket.socket) -> str:
        chunks: list[bytes] = []
        total = 0
        while total < 8_192:
            chunk = connection.recv(min(4_096, 8_192 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\0" in chunk or b"\n" in chunk:
                break
        return b"".join(chunks).decode("utf-8", errors="replace").strip("\x00\r\n")
