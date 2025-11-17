#!/usr/bin/env python3
"""Prosty serwer mockujący centralę CTIP do testów lokalnych."""
from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
from pathlib import Path

DEFAULT_SCENARIO = [
    (0.5, "RING 501 486123456"),
    (1.0, "INFO 501 486123456 CAMPAIGN=DEMO"),
    (0.8, "CONN 501 486123456"),
    (2.5, "REL 501 normal"),
    (1.2, "RING 502 486222333"),
    (0.9, "INFO 502 486222333 IVR_DIGIT=9"),
    (0.7, "CONN 502 486222333"),
    (3.0, "REL 502 busy"),
]

CRLF = "\r\n"


def parse_scenario(path: Path) -> list[tuple[float, str]]:
    scenario: list[tuple[float, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            delay_txt, payload = (line.split(";", 1) + [""])[:2]
            try:
                delay = float(delay_txt)
            except ValueError as exc:  # pragma: no cover - walidacja wejścia CLI
                raise SystemExit(f"Nieprawidłowy format linii '{line}': {exc}") from exc
            payload = payload.strip()
            if not payload:
                continue
            scenario.append((delay, payload))
    return scenario


def send_line(sock: socket.socket, line: str, lock: threading.Lock):
    data = f"{line}{CRLF}".encode("latin-1", errors="ignore")
    with lock:
        sock.sendall(data)


def streaming_worker(
    sock: socket.socket,
    scenario: list[tuple[float, str]],
    loop: bool,
    stop_event: threading.Event,
    lock: threading.Lock,
):
    try:
        while not stop_event.is_set():
            for delay, payload in scenario:
                if stop_event.wait(delay):
                    return
                try:
                    send_line(sock, payload, lock)
                except OSError:
                    stop_event.set()
                    return
            if not loop:
                break
    finally:
        stop_event.set()


def handle_client(
    sock: socket.socket,
    addr: tuple[str, int],
    scenario: list[tuple[float, str]],
    loop: bool,
    identity: str,
):
    logging.info("Nowe połączenie z %s:%s", addr[0], addr[1])
    stop_event = threading.Event()
    lock = threading.Lock()
    stream_thread: threading.Thread | None = None
    buffer = b""
    try:
        while not stop_event.is_set():
            try:
                chunk = sock.recv(1024)
            except ConnectionResetError:
                break
            if not chunk:
                break
            buffer += chunk
            while CRLF.encode() in buffer:
                line, buffer = buffer.split(CRLF.encode(), 1)
                cmd = line.decode("latin-1", errors="ignore").strip()
                upper = cmd.upper()
                logging.debug("RX: %s", upper)
                if upper == "AWHO":
                    send_line(sock, f"aOK {identity}", lock)
                elif upper.startswith("ALOGA"):
                    send_line(sock, "aOK LOGA", lock)
                    if stream_thread is None:
                        stream_thread = threading.Thread(
                            target=streaming_worker,
                            args=(sock, scenario, loop, stop_event, lock),
                            daemon=True,
                        )
                        stream_thread.start()
                else:
                    send_line(sock, "aOK", lock)
    finally:
        stop_event.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        logging.info("Zamknięto połączenie z %s:%s", addr[0], addr[1])


def serve(host: str, port: int, scenario: list[tuple[float, str]], loop: bool, identity: str):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(5)
        logging.info("Mock CTIP nasłuchuje na %s:%s", host, port)
        stop = threading.Event()

        def _handle_signal(signum, _frame):  # pragma: no cover - funkcja sygnałów
            logging.info("Otrzymano sygnał %s – zatrzymuję serwer", signum)
            stop.set()
            srv.close()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        while not stop.is_set():
            try:
                client, addr = srv.accept()
            except OSError:
                break
            threading.Thread(
                target=handle_client,
                args=(client, addr, scenario, loop, identity),
                daemon=True,
            ).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Uruchamia mock centrali CTIP do testów.")
    parser.add_argument("--host", default="127.0.0.1", help="Adres nasłuchu (domyślnie 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5525, help="Port nasłuchu (domyślnie 5525)")
    parser.add_argument(
        "--scenario-file",
        type=Path,
        help="Ścieżka do pliku scenariusza (format: sekundy;RAMKA). Gdy brak – użyj scenariusza wbudowanego.",
    )
    parser.add_argument(
        "--loop", action="store_true", help="Powtarzaj scenariusz w pętli do czasu rozłączenia"
    )
    parser.add_argument(
        "--identity",
        default="CP-000 NO03914 v1.23.0140/15",
        help="Tekst identyfikujący centralę zwracany w aOK WHO",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Poziom logowania",
    )
    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level), format="[%(levelname)s] %(message)s"
    )

    if args.scenario_file:
        scenario = parse_scenario(args.scenario_file)
        if not scenario:
            parser.error("Plik scenariusza nie zawiera poprawnych ramek.")
    else:
        scenario = DEFAULT_SCENARIO

    try:
        serve(args.host, args.port, scenario, args.loop, args.identity)
    except OSError as exc:
        logging.error("Nie udało się uruchomić mocka: %s", exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - skrypt CLI
    sys.exit(main())
