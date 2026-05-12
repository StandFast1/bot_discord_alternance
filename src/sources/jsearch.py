"""JSearch (via RapidAPI) — agrégateur LinkedIn + Indeed + ZipRecruiter + Glassdoor.

C'est THE manière propre de récupérer des offres LinkedIn et Indeed sans
violer leurs ToS — JSearch est un aggregator commercial.

- Signup : https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
- FREE tier : 200 requêtes/mois (3 cycles/jour si tu fais 1 call/cycle)
- BASIC : ~$9.99/mois pour 2500 requêtes (largement suffisant en 2h)
- PRO : ~$24.99/mois pour 10000 requêtes
- ULTRA : ~$49.99/mois pour 50000 requêtes

→ Pour 1 call toutes les 2h = 12/jour × 30 = 360/mois → tier BASIC ($10/mo) suffit.

Docs : https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


BASE = "https://jsearch.p.rapidapi.com/search"
RAPIDAPI_HOST = "jsearch.p.rapidapi.com"

QUERY = "cybersécurité alternance Paris Île-de-France"


class JSearchSource(Source):
    name = "jsearch"

    def __init__(self, rapidapi_key: str):
        self.rapidapi_key = rapidapi_key.strip()

    async def _search(self, client: httpx.AsyncClient) -> list[dict]:
        params = {
            "query": QUERY,
            "page": "1",
            "num_pages": "1",
            "country": "fr",
            "date_posted": "month",
            "employment_types": "INTERN",  # JSearch utilise INTERN pour stage/alt
        }
        r = await client.get(
            BASE,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": RAPIDAPI_HOST,
            },
            timeout=25,
        )
        if r.status_code == 429:
            log.warning("jsearch: 429 — quota RapidAPI dépassé")
            return []
        if r.status_code == 401 or r.status_code == 403:
            log.warning("jsearch: %d — clé RapidAPI invalide", r.status_code)
            return []
        if r.status_code != 200:
            log.warning("jsearch HTTP %d", r.status_code)
            return []
        try:
            return r.json().get("data") or []
        except ValueError:
            return []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        location_parts = [
            raw.get("job_city"),
            raw.get("job_state"),
            raw.get("job_country"),
        ]
        location = ", ".join(p for p in location_parts if p) or None
        return Offer(
            source="jsearch",
            external_id=raw.get("job_id"),
            title=raw.get("job_title") or "(sans titre)",
            company=raw.get("employer_name"),
            location=location,
            contract=raw.get("job_employment_type"),
            url=raw.get("job_apply_link") or raw.get("job_google_link"),
            description=raw.get("job_description"),
            posted_at=raw.get("job_posted_at_datetime_utc"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        if not self.rapidapi_key:
            return []
        raw = await self._search(client)
        return [self._to_offer(r) for r in raw if isinstance(r, dict)]
