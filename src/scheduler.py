"""Periodic scrape orchestrator."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import httpx

from .bot import AlternanceBot
from .config import Config
from .db import Database
from .excel import ExcelExporter
from .sources.base import Source


log = logging.getLogger(__name__)


class CycleResult:
    """Outcome of a scrape cycle."""

    def __init__(self):
        self.inserted_total: int = 0
        self.per_source: dict[str, dict[str, int]] = {}
        self.skipped: bool = False  # True if a cycle was already in flight


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
        self.last_run_at: Optional[datetime] = None
        self.last_run_result: Optional[CycleResult] = None
        self.next_run_at: Optional[datetime] = None

    async def run_once(self) -> CycleResult:
        """One scrape cycle. Returns a CycleResult."""
        result = CycleResult()
        if self._running:
            log.info("scrape cycle skipped: previous still running")
            result.skipped = True
            return result
        self._running = True
        try:
            async with httpx.AsyncClient(http2=False) as client:
                per_source_offers = await asyncio.gather(
                    *(s.safe_fetch(client) for s in self.sources)
                )
            all_offers = []
            for src, batch in zip(self.sources, per_source_offers):
                result.per_source.setdefault(
                    src.name, {"fetched": 0, "inserted": 0}
                )
                result.per_source[src.name]["fetched"] = len(batch)
                all_offers.extend((src.name, o) for o in batch)
            log.info("scrape cycle: %d filtered offers across %d sources",
                     len(all_offers), len(self.sources))

            inserted_ids: list[int] = []
            for src_name, offer in all_offers:
                new_id = await self.db.insert_offer(offer.to_db())
                if new_id:
                    inserted_ids.append(new_id)
                    result.per_source[src_name]["inserted"] += 1

            inserted_ids = inserted_ids[: self.cfg.max_offers_per_cycle]
            log.info("posting %d new offers to Discord", len(inserted_ids))
            for oid in inserted_ids:
                await self.bot.post_offer(oid)
                await asyncio.sleep(0.7)  # gentle on Discord rate limit
            if inserted_ids:
                await self.excel.rebuild_safe()

            result.inserted_total = len(inserted_ids)
            self.last_run_at = datetime.now(timezone.utc)
            self.last_run_result = result
            return result
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
            self.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
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
