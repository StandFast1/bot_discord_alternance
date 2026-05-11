"""Base contract every source adapter implements."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

from ..filters import fingerprint, is_alternance, is_cyber, is_idf


log = logging.getLogger(__name__)


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Offer:
    source: str
    title: str
    url: str
    external_id: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    contract: Optional[str] = None
    description: Optional[str] = None
    posted_at: Optional[str] = None
    postal_code: Optional[str] = None

    def matches_filters(self) -> bool:
        if not is_cyber(self.title, self.description or "", self.company or ""):
            return False
        if not is_idf(self.location or "", self.description or "",
                      postal_code=self.postal_code):
            return False
        text_blob = " ".join(
            t for t in (self.title, self.contract, self.description) if t
        )
        if text_blob and not is_alternance(text_blob):
            return False
        return True

    def to_db(self) -> dict:
        d = asdict(self)
        d.pop("postal_code", None)
        d["fingerprint"] = fingerprint(
            self.source, self.external_id, self.url, self.title, self.company
        )
        return d


class Source(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        ...

    async def safe_fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        try:
            offers = await self.fetch(client)
        except Exception as e:
            log.exception("source %s failed: %s", self.name, e)
            return []
        kept = [o for o in offers if o.matches_filters()]
        log.info(
            "source %s: fetched=%d kept=%d", self.name, len(offers), len(kept)
        )
        return kept
