"""France Travail (ex Pôle Emploi) Offres d'emploi v2 API.

Docs: https://francetravail.io/produits-partages/catalogue/offres-emploi
Auth: OAuth2 client_credentials, scope `api_offresdemploiv2 o2dsoffre`.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from .base import Offer, Source, USER_AGENT


AUTH_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
BASE = "https://api.francetravail.io/partenaire/offresdemploi/v2"

# E2 = apprentissage, E1 = professionnalisation
NATURE_CONTRAT = "E2,E1"

# 75=Paris, 77=Seine-et-Marne, 78=Yvelines, 91=Essonne,
# 92=Hauts-de-Seine, 93=SSD, 94=Val-de-Marne, 95=Val-d'Oise
IDF_DEPTS = ["75", "77", "78", "91", "92", "93", "94", "95"]

CYBER_QUERIES = [
    "cybersécurité",
    "sécurité informatique",
    "pentest",
    "SOC analyste",
    "DevSecOps",
    "cloud security",
    "ingénieur sécurité",
]


class FranceTravailSource(Source):
    name = "france_travail"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> Optional[str]:
        if not (self.client_id and self.client_secret):
            return None
        now = time.time()
        if self._token and now < self._token_expires - 30:
            return self._token
        r = await client.post(
            AUTH_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "api_offresdemploiv2 o2dsoffre",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_expires = now + int(data.get("expires_in", 1500))
        return self._token

    async def _search(self, client: httpx.AsyncClient, token: str,
                      mots_cles: str, dept: str) -> list[dict]:
        params = {
            "motsCles": mots_cles,
            "departement": dept,
            "natureContrat": NATURE_CONTRAT,
            "range": "0-49",
            "publieeDepuis": "7",
        }
        r = await client.get(
            f"{BASE}/offres/search",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=30,
        )
        if r.status_code == 204:
            return []
        r.raise_for_status()
        return r.json().get("resultats", [])

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        lieu = raw.get("lieuTravail") or {}
        entreprise = raw.get("entreprise") or {}
        return Offer(
            source="france_travail",
            external_id=raw.get("id"),
            title=raw.get("intitule") or "(sans titre)",
            company=entreprise.get("nom"),
            location=lieu.get("libelle"),
            postal_code=lieu.get("codePostal"),
            contract=raw.get("typeContratLibelle") or raw.get("natureContrat"),
            url=raw.get("origineOffre", {}).get("urlOrigine")
                or f"https://candidat.francetravail.fr/offres/recherche/detail/{raw.get('id')}",
            description=raw.get("description"),
            posted_at=raw.get("dateCreation"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        token = await self._get_token(client)
        if not token:
            return []
        seen_ids: set[str] = set()
        offers: list[Offer] = []
        for query in CYBER_QUERIES:
            for dept in IDF_DEPTS:
                try:
                    results = await self._search(client, token, query, dept)
                except httpx.HTTPError:
                    continue
                for raw in results:
                    rid = raw.get("id")
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    offers.append(self._to_offer(raw))
        return offers
