"""Bootstrap uslugi CTIP-Web dla Windows/NSSM."""

from __future__ import annotations

import asyncio

import uvicorn


def configure_event_loop_policy() -> None:
    """Ustawia polityke petli zgodna z uruchomieniem pod Windows."""

    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    """Uruchamia glowna aplikacje FastAPI tak jak na produkcji Windows."""

    configure_event_loop_policy()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)


if __name__ == "__main__":
    main()
