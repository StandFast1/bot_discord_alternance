"""Prospection ciblée : sortir des entreprises IDF qui POURRAIENT prendre un alternant.

On utilise l'API officielle française `recherche-entreprises.api.gouv.fr`
(beta.gouv.fr, sans clé API, gratuite, dérivée de SIRENE).

L'idée : on cible les NAF informatique/cyber, en IDF, taille TPE/PME/ETI
(grosses boîtes prennent souvent par les écoles, petites = plus réactives),
en EXCLUANT celles qui ont déjà posté une offre qu'on a scrapée.

Docs API : https://recherche-entreprises.api.gouv.fr/
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from .db import Database


log = logging.getLogger(__name__)


API_URL = "https://recherche-entreprises.api.gouv.fr/search"


# NAF (codes activité INSEE) pertinents pour cyber/IT
# https://www.insee.fr/fr/metadonnees/nafr2/sousClasse/62.01Z
NAF_CYBER_IT = [
    "62.01Z",  # Programmation informatique
    "62.02A",  # Conseil en systèmes et logiciels informatiques
    "62.02B",  # Tierce maintenance de systèmes et d'applications
    "62.03Z",  # Gestion d'installations informatiques
    "62.09Z",  # Autres activités informatiques
    "63.11Z",  # Traitement de données, hébergement
    "63.12Z",  # Portails Internet
]

# Codes tranche d'effectif INSEE :
# 11=10-19, 12=20-49, 21=50-99, 22=100-199, 31=200-249, 32=250-499, 41=500-999, 42=1000-1999
# On vise 20-499 salariés : assez gros pour avoir un budget RH, assez petit pour bouger vite
HEADCOUNT_CODES = ["12", "21", "22", "31", "32"]

# Départements IDF
IDF_DEPTS = ["75", "77", "78", "91", "92", "93", "94", "95"]


PROSPECT_STATES = (
    "new",            # vient d'être trouvé
    "to_contact",     # à démarcher
    "contacted",      # message envoyé
    "responded",      # ils ont répondu
    "interview",      # entretien obtenu
    "not_interested", # pas intéressé / NPAI
)


PROSPECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    siret TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    naf_code TEXT,
    naf_label TEXT,
    headcount TEXT,
    city TEXT,
    postal_code TEXT,
    address TEXT,
    website TEXT,
    linkedin_search TEXT,
    state TEXT NOT NULL DEFAULT 'new',
    discovered_at TEXT NOT NULL,
    state_changed_at TEXT,
    notes TEXT,
    discord_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_prospects_state ON prospects(state);
CREATE INDEX IF NOT EXISTS idx_prospects_msg ON prospects(discord_message_id);
"""


@dataclass
class Prospect:
    siret: str
    name: str
    naf_code: Optional[str] = None
    naf_label: Optional[str] = None
    headcount: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None

    @property
    def linkedin_search(self) -> str:
        # URL de recherche LinkedIn pré-filtrée pour trouver les RH/recruteurs
        # chez cette boîte. Tu cliques, tu vois les profils, tu contactes.
        q = self.name.replace('"', "")
        return (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords=RH%20OR%20recruteur%20OR%20talent&"
            f"company=\"{q}\""
        )

    def to_db(self) -> dict:
        return {
            "siret": self.siret,
            "name": self.name,
            "naf_code": self.naf_code,
            "naf_label": self.naf_label,
            "headcount": self.headcount,
            "city": self.city,
            "postal_code": self.postal_code,
            "address": self.address,
            "website": self.website,
            "linkedin_search": self.linkedin_search,
        }


class ProspectFinder:
    """Cherche des entreprises via recherche-entreprises.api.gouv.fr."""

    def __init__(self, db: Database):
        self.db = db

    async def init_db(self) -> None:
        await self.db._run(lambda conn: conn.executescript(PROSPECTS_SCHEMA))

    async def _existing_companies_in_offers(self) -> set[str]:
        """Noms de boîtes déjà présentes dans nos offres (à exclure)."""
        rows = await self.db._run(
            lambda conn: conn.execute(
                "SELECT DISTINCT LOWER(TRIM(company)) FROM offers "
                "WHERE company IS NOT NULL AND company != ''"
            ).fetchall()
        )
        return {r[0] for r in rows if r[0]}

    async def _existing_prospects(self) -> set[str]:
        rows = await self.db._run(
            lambda conn: conn.execute("SELECT siret FROM prospects").fetchall()
        )
        return {r[0] for r in rows if r[0]}

    async def _search_one(
        self, client: httpx.AsyncClient,
        naf: str, dept: str, page: int = 1,
    ) -> list[dict]:
        params = {
            "activite_principale": naf,
            "departement": dept,
            "tranche_effectif_salarie": ",".join(HEADCOUNT_CODES),
            "etat_administratif": "A",  # actives uniquement
            "per_page": 25,
            "page": page,
        }
        try:
            r = await client.get(API_URL, params=params, timeout=20)
        except httpx.HTTPError as e:
            log.warning("recherche-entreprises HTTP error %s/%s: %s", naf, dept, e)
            return []
        if r.status_code != 200:
            log.warning("recherche-entreprises HTTP %d on naf=%s dept=%s",
                        r.status_code, naf, dept)
            return []
        try:
            return r.json().get("results") or []
        except ValueError:
            return []

    @staticmethod
    def _to_prospect(raw: dict) -> Optional[Prospect]:
        siret = raw.get("siege", {}).get("siret") or raw.get("siren")
        if not siret:
            return None
        name = (
            raw.get("nom_complet")
            or raw.get("nom_raison_sociale")
            or raw.get("denomination")
            or "(sans nom)"
        ).strip()
        siege = raw.get("siege") or {}
        naf_code = raw.get("activite_principale")
        naf_label = raw.get("libelle_activite_principale")
        headcount = raw.get("tranche_effectif_salarie")
        return Prospect(
            siret=str(siret),
            name=name,
            naf_code=naf_code,
            naf_label=naf_label,
            headcount=headcount,
            city=siege.get("libelle_commune") or siege.get("commune"),
            postal_code=siege.get("code_postal"),
            address=siege.get("adresse"),
            website=raw.get("site_web") or None,
        )

    async def find_new(self, limit: int = 30) -> list[int]:
        """Cherche jusqu'à `limit` nouvelles boîtes à prospecter.

        Renvoie les IDs DB des prospects insérés (donc nouveaux).
        """
        excluded_companies = await self._existing_companies_in_offers()
        existing_siret = await self._existing_prospects()
        log.info(
            "prospect search: %d known offers companies, %d existing prospects",
            len(excluded_companies), len(existing_siret),
        )

        new_ids: list[int] = []
        async with httpx.AsyncClient() as client:
            for naf in NAF_CYBER_IT:
                for dept in IDF_DEPTS:
                    if len(new_ids) >= limit:
                        break
                    results = await self._search_one(client, naf, dept)
                    for raw in results:
                        if len(new_ids) >= limit:
                            break
                        p = self._to_prospect(raw)
                        if p is None:
                            continue
                        if p.siret in existing_siret:
                            continue
                        if p.name.lower().strip() in excluded_companies:
                            log.debug("skip %s (already in offers)", p.name)
                            continue
                        new_id = await self._insert(p)
                        if new_id:
                            new_ids.append(new_id)
                            existing_siret.add(p.siret)
                if len(new_ids) >= limit:
                    break
        log.info("prospect search: %d new prospects inserted", len(new_ids))
        return new_ids

    async def _insert(self, p: Prospect) -> Optional[int]:
        now = datetime.now(timezone.utc).isoformat()

        def _do(conn):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO prospects
                  (siret, name, naf_code, naf_label, headcount, city,
                   postal_code, address, website, linkedin_search,
                   state, discovered_at, state_changed_at)
                VALUES (:siret, :name, :naf_code, :naf_label, :headcount,
                        :city, :postal_code, :address, :website,
                        :linkedin_search, 'new', :now, :now)
                """,
                {**p.to_db(), "now": now},
            )
            return cur.lastrowid if cur.rowcount else None

        return await self.db._run(_do)

    async def get(self, prospect_id: int):
        return await self.db._run(
            lambda conn: conn.execute(
                "SELECT * FROM prospects WHERE id = ?", (prospect_id,)
            ).fetchone()
        )

    async def get_by_message(self, message_id: int):
        return await self.db._run(
            lambda conn: conn.execute(
                "SELECT * FROM prospects WHERE discord_message_id = ?",
                (str(message_id),),
            ).fetchone()
        )

    async def set_message_id(self, prospect_id: int, message_id: int) -> None:
        await self.db._run(
            lambda conn: conn.execute(
                "UPDATE prospects SET discord_message_id = ? WHERE id = ?",
                (str(message_id), prospect_id),
            )
        )

    async def set_state(self, prospect_id: int, state: str) -> None:
        if state not in PROSPECT_STATES:
            raise ValueError(f"invalid state: {state}")
        now = datetime.now(timezone.utc).isoformat()
        await self.db._run(
            lambda conn: conn.execute(
                "UPDATE prospects SET state = ?, state_changed_at = ? "
                "WHERE id = ?",
                (state, now, prospect_id),
            )
        )

    async def list_by_state(self, state: str, limit: int = 50):
        return await self.db._run(
            lambda conn: conn.execute(
                "SELECT * FROM prospects WHERE state = ? "
                "ORDER BY discovered_at DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        )

    async def stats(self) -> dict[str, int]:
        def _do(conn):
            rows = conn.execute(
                "SELECT state, COUNT(*) as n FROM prospects GROUP BY state"
            ).fetchall()
            out = {s: 0 for s in PROSPECT_STATES}
            for r in rows:
                out[r["state"]] = r["n"]
            return out
        return await self.db._run(_do)
