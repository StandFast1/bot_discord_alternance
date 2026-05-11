"""HelloWork: HTML scraping of the public listings.

Public URL pattern:
  https://www.hellowork.com/fr-fr/emploi/recherche.html
    ?k=<keyword>&c=Alternance&l=Ile-de-France

Selectors target stable data-* and itemprop attributes that have been
in place for a long time; if HelloWork redesigns, only this file changes.
"""
from __future__ import annotations

import urllib.parse

import httpx
from selectolax.parser import HTMLParser

from .base import Offer, Source, USER_AGENT


SEARCH = "https://www.hellowork.com/fr-fr/emploi/recherche.html"

KEYWORDS = [
    "cybersécurité",
    "pentest",
    "SOC",
    "DevSecOps",
    "sécurité informatique",
]


class HelloWorkSource(Source):
    name = "hellowork"

    async def _fetch_page(self, client: httpx.AsyncClient, kw: str,
                         page: int) -> str:
        params = {
            "k": kw,
            "c": "Alternance",
            "l": "Ile-de-France",
            "p": page,
        }
        r = await client.get(
            SEARCH,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
            timeout=20,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return ""
        return r.text

    @staticmethod
    def _parse(html: str) -> list[Offer]:
        if not html:
            return []
        tree = HTMLParser(html)
        offers: list[Offer] = []
        # HelloWork uses <li data-id-storage-target="item"> for each card
        # and a parent <a> wrapping the entire card.
        for card in tree.css("li[data-id-storage-target='item']"):
            link_node = card.css_first("a[href*='/emplois/']")
            if not link_node:
                continue
            href = link_node.attributes.get("href", "")
            if not href:
                continue
            url = urllib.parse.urljoin("https://www.hellowork.com", href)

            title_node = card.css_first("h3") or link_node
            title = (title_node.text(strip=True) if title_node else "").strip()
            if not title:
                continue

            company_node = card.css_first("[data-cy='companyName']") \
                or card.css_first("p.tw-text-grey-9")
            company = company_node.text(strip=True) if company_node else None

            location_node = card.css_first("[data-cy='localisationCard']") \
                or card.css_first("div.tw-flex.tw-items-center")
            location = location_node.text(strip=True) if location_node else None

            ext_id = href.rstrip("/").split("/")[-1].split(".")[0]
            offers.append(Offer(
                source="hellowork",
                external_id=ext_id,
                title=title,
                company=company,
                location=location,
                contract="Alternance",
                url=url,
            ))
        return offers

    async def fetch(self, client: httpx.AsyncClient) -> list[Offer]:
        out: list[Offer] = []
        seen: set[str] = set()
        for kw in KEYWORDS:
            for page in (1, 2):
                html = await self._fetch_page(client, kw, page)
                for o in self._parse(html):
                    if o.url in seen:
                        continue
                    seen.add(o.url)
                    out.append(o)
        return out
