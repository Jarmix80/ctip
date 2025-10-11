"""
Lekki klient CTIP służący do ręcznego monitorowania ramek centrali Slican.

Skrypt nawiązuje połączenie TCP, wykonuje sekwencję `aWHO`/`aLOGA <PIN>` i wypisuje
kolejne linie protokołu CTIP aż do przerwania połączenia lub wywołania Ctrl+C.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from collections.abc import Generator
from datetime import datetime

from log_utils import append_log

PAYLOAD_ENCODING = os.getenv("PAYLOAD_ENCODING", "latin-1")
PBX_HOST = os.getenv("PBX_HOST", "192.168.0.11")
PBX_PORT = int(os.getenv("PBX_PORT", "5524"))
PBX_PIN = os.getenv("PBX_PIN", "1234")
SOCK_CONNECT_TIMEOUT = int(os.getenv("SOCK_CONNECT_TIMEOUT", "5"))
SOCK_RECV_TIMEOUT = int(os.getenv("SOCK_RECV_TIMEOUT", "5"))

LOG_SUBDIR = "Centralka"
LOG_BASE = "log_con_sli"
LOG_PREFIX = "[CTIP.MON]"


def log_event(message: str, *, prefix: str | None = LOG_PREFIX, error: bool = False) -> None:
    now = datetime.now()
    text = f"{prefix} {message}" if prefix else message
    append_log(LOG_SUBDIR, LOG_BASE, text, now=now)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    stream = sys.stderr if error else sys.stdout
    print(f"[{ts}] {text}", file=stream, flush=True)


def send_command(sock: socket.socket, command: str) -> None:
    """Wysyła polecenie CTIP z wymaganym prefiksem `a`."""
    cmd = (command or "").strip()
    if not cmd:
        return
    if not cmd.lower().startswith("a"):
        cmd = f"a{cmd}"
    payload = f"{cmd}\r\n".encode("ascii", errors="ignore")
    sock.sendall(payload)


def iter_lines(sock: socket.socket) -> Generator[str, None, None]:
    """Generator zwracający odkodowane linie zakończone CRLF."""
    buffer = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            continue
        if not chunk:
            raise ConnectionError("gniazdo zostało zamknięte przez centralę")
        buffer += chunk
        while b"\r\n" in buffer:
            line, buffer = buffer.split(b"\r\n", 1)
            yield line.decode(PAYLOAD_ENCODING, errors="ignore").strip()


def run_monitor() -> None:
    """Łączy się z centralą CTIP i wypisuje kolejne zdarzenia na STDOUT."""
    sock = socket.create_connection(
        (PBX_HOST, PBX_PORT),
        timeout=SOCK_CONNECT_TIMEOUT,
    )
    try:
        sock.settimeout(SOCK_RECV_TIMEOUT)
        log_event(f"Połączono z {PBX_HOST}:{PBX_PORT}")

        send_command(sock, "WHO")
        time.sleep(0.2)
        send_command(sock, f"LOGA {PBX_PIN}")

        for line in iter_lines(sock):
            log_event(line, prefix=None)
    finally:
        sock.close()


def main() -> None:
    try:
        run_monitor()
    except KeyboardInterrupt:
        log_event("Przerwano przez użytkownika", prefix=f"{LOG_PREFIX} [WARN]", error=True)
    except Exception as exc:
        log_event(f"Błąd: {exc}", prefix=f"{LOG_PREFIX} [ERR]", error=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
