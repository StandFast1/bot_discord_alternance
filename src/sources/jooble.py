"""Jooble API — aggregator international avec focus FR honorable.

- Signup gratuit : https://jooble.org/api/about → "Get API key"
- Tu reçois une clé par email
- Limites pas officiellement chiffrées mais raisonnables (quelques milliers/mois)
- Docs : https://jooble.org/api/about

Endpoint : POST https://jooble.org/api/<api_key>
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


BASE = "https://jooble.org/api"

KEYWORDS = "cybersécurité alternance"
LOCATION = "Île-de-France"


class JoobleSource(Source):
    name = "jooble"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    async def _search(self, client: httpx.AsyncClient) -> list[dict]:
        url = f"{BASE}/{self.api_key}"
        body = {
            "keywords": KEYWORDS,
            "location": LOCATION,
            "page": 1,
            "ResultOnPage": 50,
            "datecreatedfrom": "",  # vide = pas de filtre date
            "SearchMode": 1,
        }
        r = await client.post(
            url,
            json=body,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        if r.status_code == 401 or r.status_code == 403:
            log.warning("jooble: %d — clé invalide ou expirée", r.status_code)
            return []
        if r.status_code != 200:
            log.warning("jooble HTTP %d", r.status_code)
            return []
        try:
            return r.json().get("jobs") or []
        except ValueError:
            return []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        return Offer(
            source="jooble",
            external_id=str(raw.get("id")) if raw.get("id") else None,
            title=raw.get("title") or "(sans titre)",
            company=raw.get("company"),
            location=raw.get("location"),
            contract=raw.get("type") or "Alternance",
            url=raw.get("link"),
            description=raw.get("snippet"),
            posted_at=raw.get("updated"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        if not self.api_key:
            return []
        raw = await self._search(client)
        return [self._to_offer(r) for r in raw if isinstance(r, dict)]
