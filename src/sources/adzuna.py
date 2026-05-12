"""Adzuna API — aggregator multi-pays, gratuit avec key.

- Free tier : 1000 requêtes/mois (largement assez avec 1 call par cycle)
- Signup : https://developer.adzuna.com → "Sign up for Free"
- Tu obtiens un `app_id` (court) et un `app_key` (long)
- Docs : https://developer.adzuna.com/docs/search

Endpoint search FR : /v1/api/jobs/fr/search/<page>
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


BASE = "https://api.adzuna.com/v1/api/jobs/fr/search"

KEYWORDS = "cybersécurité OR pentest OR SOC OR DevSecOps OR sécurité informatique"
LOCATION = "Île-de-France"
# category code adzuna : "it-jobs" couvre tech/cyber
CATEGORY = "it-jobs"


class AdzunaSource(Source):
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str):
        self.app_id = app_id.strip()
        self.app_key = app_key.strip()

    async def _search(self, client: httpx.AsyncClient, page: int = 1) -> list[dict]:
        url = f"{BASE}/{page}"
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": 50,
            "what": KEYWORDS,
            "where": LOCATION,
            "category": CATEGORY,
            "content-type": "application/json",
            "max_days_old": 14,
        }
        r = await client.get(
            url,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=25,
        )
        if r.status_code == 401:
            log.warning("adzuna: 401 — vérifier APP_ID et APP_KEY")
            return []
        if r.status_code == 429:
            log.warning("adzuna: 429 rate-limited (quota mensuel ?)")
            return []
        if r.status_code != 200:
            log.warning("adzuna HTTP %d", r.status_code)
            return []
        try:
            return r.json().get("results") or []
        except ValueError:
            return []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        loc = raw.get("location") or {}
        return Offer(
            source="adzuna",
            external_id=str(raw.get("id")) if raw.get("id") else None,
            title=raw.get("title") or "(sans titre)",
            company=(raw.get("company") or {}).get("display_name"),
            location=loc.get("display_name") if isinstance(loc, dict) else str(loc),
            contract=raw.get("contract_time") or raw.get("contract_type"),
            url=raw.get("redirect_url") or raw.get("adref"),
            description=raw.get("description"),
            posted_at=raw.get("created"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        if not (self.app_id and self.app_key):
            return []
        raw = await self._search(client, page=1)
        return [self._to_offer(r) for r in raw if isinstance(r, dict)]
