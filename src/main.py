"""Entry point: wires everything together."""
from __future__ import annotations

import asyncio
import logging
import signal

from .bot import AlternanceBot
from .config import Config
from .db import Database
from .scheduler import Scraper
from .sources import (
    ApecSource,
    FranceTravailSource,
    HelloWorkSource,
    WTTJSource,
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_sources(cfg: Config) -> list:
    return [
        FranceTravailSource(
            client_id=cfg.france_travail_client_id,
            client_secret=cfg.france_travail_client_secret,
        ),
        HelloWorkSource(),
        WTTJSource(),
        ApecSource(),
    ]


async def amain() -> None:
    setup_logging()
    log = logging.getLogger("main")

    cfg = Config.load()
    db = Database(cfg.db_path)
    await db.init()
    log.info("db ready at %s", cfg.db_path)

    bot = AlternanceBot(cfg, db)
    sources = build_sources(cfg)
    scraper = Scraper(cfg, db, bot, sources)

    bot.set_scrape_callback(scraper.run_once)
    bot.set_on_ready(scraper.loop_forever)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop_signal(signame: str) -> None:
        log.info("received %s, shutting down", signame)
        scraper.stop()
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, sig_name):
            try:
                loop.add_signal_handler(
                    getattr(signal, sig_name),
                    lambda n=sig_name: _stop_signal(n),
                )
            except NotImplementedError:
                # Windows: signal handlers not supported on the asyncio loop
                pass

    try:
        await bot.start(cfg.discord_token)
    finally:
        scraper.stop()
        if not bot.is_closed():
            await bot.close()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
