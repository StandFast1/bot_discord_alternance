"""École 2600 — Grimp.io (https://ecole2600.grimp.io).

Grimp est une plateforme SaaS de recrutement utilisée par plusieurs écoles
(2600, 42, etc.). L'app frontend appelle une API JSON. On copie ton cookie
de session depuis le navigateur, le bot l'utilise pour fetcher les offres.

# Trouver l'endpoint exact

1. Connecte-toi sur https://ecole2600.grimp.io
2. Va sur la page liste des offres
3. Ouvre DevTools (F12) → onglet **Network** → filtre **Fetch/XHR**
4. Recharge la page
5. Cherche une requête qui renvoie du JSON avec une liste d'offres
   (en général une URL du genre `/api/offers`, `/api/v1/jobs`, etc.)
6. Note :
   - L'URL exacte
   - Les paramètres de query string (?contract=alternance&...)
   - La structure du JSON renvoyé (clés "title", "company", "url", etc.)
   - Le nom du cookie d'auth (souvent `_grimp_session`, `session_id`, etc.)

# Configurer le cookie

Dans GitHub Secret `GRIMP_COOKIE`, colle la valeur complète du cookie de
session (le format header `Cookie:` : `name1=val1; name2=val2`).

Le cookie expire généralement après quelques semaines — quand le bot
loggera des 401/403, tu le rafraîchis depuis ton navigateur.
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


# ⚠️ À AJUSTER après inspection DevTools (voir docstring du module)
API_BASE = "https://ecole2600.grimp.io"
LIST_PATH = "/api/offers"  # URL exacte à vérifier
LIST_PARAMS = {
    # Adapter selon ce que le frontend envoie. Exemples plausibles :
    # "contract_type": "apprenticeship",
    # "per_page": 50,
}


class GrimpSource(Source):
    name = "grimp_ecole2600"

    def __init__(self, cookie: str):
        self.cookie = cookie.strip()

    def _headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Cookie": self.cookie,
            "Referer": f"{API_BASE}/",
            "X-Requested-With": "XMLHttpRequest",
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
                "grimp auth expired (HTTP %d) — refresh GRIMP_COOKIE secret",
                r.status_code,
            )
            return []
        if r.status_code != 200:
            log.warning("grimp HTTP %d on %s", r.status_code, url)
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("grimp non-JSON response (cookie expired?)")
            return []

        # Adapter selon la structure réelle. Cas typiques :
        #   data["data"], data["offers"], data["results"], ou data directement
        for key in ("offers", "data", "results", "items", "jobs"):
            if isinstance(data, dict) and key in data and isinstance(data[key], list):
                return data[key]
        return data if isinstance(data, list) else []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        """Adapter selon les clés réelles du JSON Grimp.

        Les noms de champs ci-dessous sont des suppositions plausibles
        basées sur d'autres déploiements Grimp. Vérifie dans DevTools.
        """
        ext_id = str(raw.get("id") or raw.get("uuid") or "")
        title = raw.get("title") or raw.get("name") or "(sans titre)"
        company = (
            raw.get("company_name")
            or (raw.get("company") or {}).get("name")
            or raw.get("organization")
        )
        location = (
            raw.get("location")
            or raw.get("city")
            or ", ".join(raw.get("locations") or [])
            or None
        )
        contract = (
            raw.get("contract_type")
            or raw.get("contract")
            or "Alternance"
        )
        slug = raw.get("slug") or ext_id
        url = (
            raw.get("url")
            or raw.get("public_url")
            or f"{API_BASE}/offers/{slug}"
        )
        return Offer(
            source="grimp_ecole2600",
            external_id=ext_id or None,
            title=title,
            company=company,
            location=location,
            contract=contract,
            url=url,
            description=raw.get("description") or raw.get("summary"),
            posted_at=raw.get("published_at") or raw.get("created_at"),
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
            return []  # silently skip if not configured
        raw_offers = await self._fetch_listing(client)
        return [self._to_offer(r) for r in raw_offers if isinstance(r, dict)]
