"""Periodic scrape orchestrator."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import httpx

from .bot import AlternanceBot
from .config import Config
from .db import Database
from .excel import ExcelExporter
from .sources.base import Source


log = logging.getLogger(__name__)


class Scraper:
    def __init__(self, cfg: Config, db: Database, bot: AlternanceBot,
                 sources: Iterable[Source], excel: ExcelExporter):
        self.cfg = cfg
        self.db = db
        self.bot = bot
        self.sources = list(sources)
        self.excel = excel
        self._running = False
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        """One scrape cycle. Returns count of offers newly inserted."""
        if self._running:
            log.info("scrape cycle skipped: previous still running")
            return 0
        self._running = True
        try:
            async with httpx.AsyncClient(http2=False) as client:
                results = await asyncio.gather(
                    *(s.safe_fetch(client) for s in self.sources)
                )
            all_offers = [o for batch in results for o in batch]
            log.info("scrape cycle: %d filtered offers across %d sources",
                     len(all_offers), len(self.sources))

            inserted_ids: list[int] = []
            for offer in all_offers:
                new_id = await self.db.insert_offer(offer.to_db())
                if new_id:
                    inserted_ids.append(new_id)

            inserted_ids = inserted_ids[: self.cfg.max_offers_per_cycle]
            log.info("posting %d new offers to Discord", len(inserted_ids))
            for oid in inserted_ids:
                await self.bot.post_offer(oid)
                await asyncio.sleep(0.7)  # gentle on Discord rate limit
            if inserted_ids:
                await self.excel.rebuild_safe()
            return len(inserted_ids)
        finally:
            self._running = False

    async def loop_forever(self) -> None:
        interval = max(60, self.cfg.scrape_interval_hours * 3600)
        log.info("scraper loop starting; interval=%ds", interval)
        try:
            await self.run_once()
        except Exception as e:
            log.exception("initial scrape failed: %s", e)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            try:
                await self.run_once()
            except Exception as e:
                log.exception("scrape cycle failed: %s", e)

    def stop(self) -> None:
        self._stop.set()
