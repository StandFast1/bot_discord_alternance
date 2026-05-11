"""École 2600 — Bluebox (https://bluebox.2600.eu).

SPA custom de l'école (route #/etudiant/annonces). Auth par cookie.
Même approche que Grimp : tu copies ton cookie de session depuis ton
navigateur dans le secret BLUEBOX_COOKIE.

# Inspection à faire

1. Connecte-toi sur https://bluebox.2600.eu
2. Navigue vers la page des annonces (#/etudiant/annonces)
3. DevTools (F12) → **Network** → **Fetch/XHR**
4. Recharge — tu verras les appels API que le SPA fait pour récupérer
   les offres. URL probable : `/api/annonces`, `/api/v1/jobs`, etc.
5. Note l'URL, les params, les clés JSON, et le nom du cookie de session.

# Configurer le cookie

Secret GitHub `BLUEBOX_COOKIE` : valeur complète du header Cookie
(`name1=val1; name2=val2`).
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


# ⚠️ À AJUSTER après inspection DevTools
API_BASE = "https://bluebox.2600.eu"
LIST_PATH = "/api/annonces"  # à vérifier — peut être /api/v1/annonces, etc.
LIST_PARAMS = {
    # Filtres typiques d'un job board d'école :
    # "type": "alternance",
    # "actif": "true",
}


class BlueboxSource(Source):
    name = "bluebox_2600"

    def __init__(self, cookie: str):
        self.cookie = cookie.strip()

    def _headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Cookie": self.cookie,
            "Referer": f"{API_BASE}/",
        }

    async def _fetch_listing(self, client: httpx.AsyncClient) -> list[dict]:
        url = f"{API_BASE}{LIST_PATH}"
        r = await client.get(
            url,
            params=LIST_PARAMS,
            headers=self._headers(),
            timeout=25,
            follow_redirects=False,
        )
        if r.status_code in (401, 403):
            log.warning(
                "bluebox auth expired (HTTP %d) — refresh BLUEBOX_COOKIE secret",
                r.status_code,
            )
            return []
        if r.status_code != 200:
            log.warning("bluebox HTTP %d on %s", r.status_code, url)
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("bluebox non-JSON response (cookie expired?)")
            return []

        for key in ("annonces", "data", "results", "items", "offres"):
            if isinstance(data, dict) and key in data and isinstance(data[key], list):
                return data[key]
        return data if isinstance(data, list) else []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        """Adapter selon les clés réelles du JSON Bluebox."""
        ext_id = str(raw.get("id") or raw.get("_id") or "")
        title = (
            raw.get("titre")
            or raw.get("title")
            or raw.get("intitule")
            or "(sans titre)"
        )
        company = (
            raw.get("entreprise")
            or raw.get("societe")
            or raw.get("company")
            or (raw.get("organisation") or {}).get("nom")
        )
        location = (
            raw.get("ville")
            or raw.get("lieu")
            or raw.get("location")
        )
        contract = (
            raw.get("type_contrat")
            or raw.get("contract")
            or "Alternance"
        )
        url = (
            raw.get("url")
            or f"{API_BASE}/#/etudiant/annonces/{ext_id}"
        )
        return Offer(
            source="bluebox_2600",
            external_id=ext_id or None,
            title=title,
            company=company,
            location=location,
            contract=contract,
            url=url,
            description=raw.get("description") or raw.get("contenu"),
            posted_at=raw.get("date_publication") or raw.get("created_at"),
        )

    async def safe_fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        """Override: école 2600 = cyber par définition, on garde tout."""
        try:
            offers = await self.fetch(client)
        except Exception as e:
            log.exception("source %s failed: %s", self.name, e)
            return []
        log.info("source %s: kept all %d (school-internal)", self.name, len(offers))
        return offers

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        if not self.cookie:
            return []
        raw_offers = await self._fetch_listing(client)
        return [self._to_offer(r) for r in raw_offers if isinstance(r, dict)]
