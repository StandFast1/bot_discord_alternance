"""APEC: undocumented but stable public search API used by apec.fr.

Endpoint /cms/webservices/rechercheOffre/list returns paginated JSON.
Same backend the site's React app calls — no auth.
"""
from __future__ import annotations

import httpx

from .base import Offer, Source, USER_AGENT


API_URL = "https://www.apec.fr/cms/webservices/rechercheOffre/list"

KEYWORDS = [
    "cybersécurité",
    "sécurité informatique",
    "pentest",
    "SOC",
    "DevSecOps",
]

# APEC location filters (geographic ids for IDF region)
PAYLOAD_LOCATIONS = [
    {"libelle": "Ile-de-France", "code": "1", "type": "REGION"},
]

# Contract codes: 102 = alternance / contrat pro
CONTRACT_CODES = ["102"]


class ApecSource(Source):
    name = "apec"

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        payload = {
            "motsCles": query,
            "typesContrat": CONTRACT_CODES,
            "lieux": PAYLOAD_LOCATIONS,
            "sortsType": "SCORE",
            "sortsDirection": "DESCENDING",
            "pagination": {"range": {"startAt": 0, "endAt": 30}},
        }
        r = await client.post(
            API_URL,
            json=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.apec.fr",
                "Referer": "https://www.apec.fr/candidat/recherche-emploi.html",
            },
            timeout=25,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("resultats") or data.get("results") or []

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        ref = raw.get("numeroOffre") or raw.get("offreId") or raw.get("id")
        slug = raw.get("intituleSEO") or raw.get("slug") or "offre"
        url = (
            raw.get("urlOffre")
            or f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{ref}"
        )
        location = raw.get("lieuTexte") or (
            ", ".join(raw.get("lieux", [])) if isinstance(raw.get("lieux"), list) else None
        )
        return Offer(
            source="apec",
            external_id=str(ref) if ref else None,
            title=raw.get("intitule") or "(sans titre)",
            company=raw.get("nomCommercial") or raw.get("nomEntreprise"),
            location=location,
            contract=raw.get("typeContrat") or "Alternance",
            url=url,
            description=raw.get("texteOffre") or raw.get("descriptif"),
            posted_at=raw.get("datePublication"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        seen: set[str] = set()
        out: list[Offer] = []
        for kw in KEYWORDS:
            try:
                results = await self._search(client, kw)
            except httpx.HTTPError:
                continue
            for raw in results:
                rid = str(raw.get("numeroOffre") or raw.get("id") or "")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                out.append(self._to_offer(raw))
        return out
