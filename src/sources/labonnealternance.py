"""La Bonne Alternance (beta.gouv.fr) — API officielle française.

C'est LA source la plus pertinente pour de l'alternance en France :
- Officielle (service.gouv.fr / beta.gouv.fr)
- Gratuite, sans clé API
- Spécifique alternance (contrats apprentissage + pro)
- Couvre toute la France, géocodage par insee/postal/region
- Mise à jour quotidienne par France Travail + partenaires

Docs : https://labonnealternance.apprentissage.beta.gouv.fr/api-docs/

# Comment trouver les bons paramètres

- `romes` : codes ROME des métiers visés. Pour cyber :
    M1801 = Administration de systèmes d'information
    M1802 = Expertise et support en systèmes d'information
    M1810 = Production et exploitation de systèmes d'information
    H1206 = Management et ingénierie études, recherche développement industriel
- `insee` ou `latitude`/`longitude` : centre géographique
- `radius` : rayon de recherche en km
"""
from __future__ import annotations

import logging

import httpx

from .base import Offer, Source, USER_AGENT


log = logging.getLogger(__name__)


# Endpoint v1 public (beta.gouv) — vérifier sur api-docs si change
BASE = "https://labonnealternance.apprentissage.beta.gouv.fr"
SEARCH_PATH = "/api/v1/jobs"

# ROME cyber/IT
ROME_CYBER = ["M1801", "M1802", "M1810", "H1206"]

# Coordonnées Paris centre, rayon 50km couvre toute l'IDF
LAT_PARIS = 48.8566
LON_PARIS = 2.3522
RADIUS_KM = 50


class LaBonneAlternanceSource(Source):
    name = "labonnealternance"

    async def _search(self, client: httpx.AsyncClient) -> list[dict]:
        params = {
            "romes": ",".join(ROME_CYBER),
            "latitude": LAT_PARIS,
            "longitude": LON_PARIS,
            "radius": RADIUS_KM,
            "sources": "matcha,offres,lba",  # toutes les sources internes
            "caller": "alternance-bot-discord",
        }
        url = f"{BASE}{SEARCH_PATH}"
        r = await client.get(
            url,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning("labonnealternance HTTP %d", r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        # La structure de réponse comporte plusieurs blocs :
        # matchas (offres LBA), peJobs (offres PE), lbaCompanies (sociétés).
        # On ne garde que les offres concrètes.
        offers: list[dict] = []
        if isinstance(data, dict):
            for key in ("matchas", "peJobs", "jobs"):
                section = data.get(key)
                if isinstance(section, dict):
                    results = section.get("results") or []
                    offers.extend(r for r in results if isinstance(r, dict))
                elif isinstance(section, list):
                    offers.extend(r for r in section if isinstance(r, dict))
        return offers

    @staticmethod
    def _to_offer(raw: dict) -> Offer:
        # La structure varie selon la source interne (matchas, pe...)
        # On gère les cas connus avec des fallback.
        company = (
            raw.get("company", {}).get("name") if isinstance(raw.get("company"), dict)
            else raw.get("company_name") or raw.get("entreprise")
        )
        location_obj = raw.get("place") or raw.get("location") or {}
        if isinstance(location_obj, dict):
            location = location_obj.get("fullAddress") or location_obj.get("city")
            postal_code = location_obj.get("zipCode") or location_obj.get("cp")
        else:
            location = str(location_obj) if location_obj else None
            postal_code = None

        job_obj = raw.get("job") or {}
        title = (
            (job_obj.get("title") if isinstance(job_obj, dict) else None)
            or raw.get("title")
            or raw.get("intitule")
            or "(sans titre)"
        )
        ext_id = str(
            raw.get("id")
            or (job_obj.get("id") if isinstance(job_obj, dict) else None)
            or ""
        )
        url = (
            raw.get("url")
            or raw.get("contact", {}).get("url") if isinstance(raw.get("contact"), dict)
            else f"{BASE}/recherche-apprentissage?display=list&job={ext_id}"
        )
        return Offer(
            source="labonnealternance",
            external_id=ext_id or None,
            title=title,
            company=company,
            location=location,
            postal_code=postal_code,
            contract="Alternance",
            url=url if isinstance(url, str) else str(url),
            description=(
                (job_obj.get("description") if isinstance(job_obj, dict) else None)
                or raw.get("description")
            ),
            posted_at=(
                (job_obj.get("creationDate") if isinstance(job_obj, dict) else None)
                or raw.get("createdAt")
            ),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        raw = await self._search(client)
        return [self._to_offer(r) for r in raw if isinstance(r, dict)]
