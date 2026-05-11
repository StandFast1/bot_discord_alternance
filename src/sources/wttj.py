"""Welcome to the Jungle: public Algolia search index.

WTTJ exposes a public Algolia search index used by their own SPA.
Keys below are the public (browser-side) keys — they are visible in any
WTTJ page source. Using them is the same as a user browsing the site.

If WTTJ rotates these public keys, fetch the bundled JS and re-extract,
or fall back to a Playwright run.
"""
from __future__ import annotations

import httpx

from .base import Offer, Source, USER_AGENT


ALGOLIA_APP_ID = "CSEPY0AIE6"
ALGOLIA_API_KEY = "0c41cfaaff3d3affe71ec0ff3a51b7e1"
ALGOLIA_INDEX = "wk_live_jobs"
ALGOLIA_URL = (
    f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
)

KEYWORDS = [
    "cybersécurité",
    "cybersecurity",
    "pentest",
    "SOC",
    "DevSecOps",
    "sécurité informatique",
]

IDF_FACETS = [
    "offices.country_code:FR",
]


class WTTJSource(Source):
    name = "wttj"

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        body = {
            "query": query,
            "hitsPerPage": 30,
            "facetFilters": [
                ["contract_type:apprenticeship",
                 "contract_type:internship",
                 "contract_type:professionalization"],
                IDF_FACETS,
            ],
            "aroundLatLng": "48.8566,2.3522",
            "aroundRadius": 60000,
        }
        r = await client.post(
            ALGOLIA_URL,
            params={
                "x-algolia-application-id": ALGOLIA_APP_ID,
                "x-algolia-api-key": ALGOLIA_API_KEY,
            },
            json=body,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return []
        return r.json().get("hits", [])

    @staticmethod
    def _to_offer(hit: dict) -> Offer:
        slug = hit.get("slug") or hit.get("reference") or hit.get("objectID")
        org_slug = (hit.get("organization") or {}).get("slug", "")
        url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{slug}"
        offices = hit.get("offices") or []
        loc = ", ".join(
            o.get("city") or o.get("country") or "" for o in offices if o
        ).strip(", ")
        return Offer(
            source="wttj",
            external_id=str(hit.get("objectID")),
            title=hit.get("name") or "(sans titre)",
            company=(hit.get("organization") or {}).get("name"),
            location=loc or None,
            contract=hit.get("contract_type"),
            url=url,
            description=hit.get("profile") or hit.get("description"),
            posted_at=hit.get("published_at"),
        )

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        seen: set[str] = set()
        out: list[Offer] = []
        for kw in KEYWORDS:
            try:
                hits = await self._search(client, kw)
            except httpx.HTTPError:
                continue
            for h in hits:
                oid = str(h.get("objectID"))
                if oid in seen:
                    continue
                seen.add(oid)
                out.append(self._to_offer(h))
        return out
