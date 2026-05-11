"""Cyber keyword + IDF geo filter, plus stable fingerprint for dedup."""
from __future__ import annotations

import hashlib
import re
import unicodedata


CYBER_KEYWORDS = [
    "cyber", "cybersecurite", "cybersecurity", "securite informatique",
    "securite des si", "securite si", "infosec",
    "pentest", "pentester", "redteam", "red team", "offensive security",
    "soc", "secops", "ciso", "rssi", "iam", "pam",
    "siem", "edr", "xdr", "soar", "dlp",
    "devsecops", "sec ops", "cloud security", "cloud sec",
    "blue team", "incident response", "threat intel", "threat hunting",
    "forensic", "forensics", "malware", "reverse engineering",
    "ethical hacking", "vulnerability", "vulnerabilite",
    "grc", "iso 27001", "ebios", "risk management",
    "appsec", "application security", "secure code",
    "audit securite", "audit sec",
]


IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

IDF_CITY_HINTS = [
    "paris", "ile-de-france", "ile de france", "iledefrance",
    "nanterre", "boulogne", "saint-denis", "saint denis", "montreuil",
    "versailles", "cergy", "evry", "melun", "bobigny", "creteil",
    "courbevoie", "issy-les-moulineaux", "issy les moulineaux",
    "neuilly", "levallois", "puteaux", "saint-ouen", "saint ouen",
    "la defense", "defense", "vincennes", "ivry",
    "argenteuil", "asnieres", "champigny",
]


ALTERNANCE_HINTS = [
    "alternance", "alternant", "alternante",
    "apprentissage", "apprenti", "apprentie",
    "contrat pro", "contrat de professionnalisation",
    "professionnalisation",
]


def _norm(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def is_cyber(*texts: str) -> bool:
    blob = " ".join(_norm(t) for t in texts if t)
    if not blob:
        return False
    for kw in CYBER_KEYWORDS:
        if _norm(kw) in blob:
            return True
    return False


def is_idf(*texts: str, postal_code: str | None = None) -> bool:
    if postal_code and postal_code[:2] in IDF_DEPARTMENTS:
        return True
    blob = " ".join(_norm(t) for t in texts if t)
    if not blob:
        return False
    if re.search(r"\b(75|77|78|91|92|93|94|95)\d{3}\b", blob):
        return True
    for hint in IDF_CITY_HINTS:
        if _norm(hint) in blob:
            return True
    return False


def is_alternance(*texts: str) -> bool:
    blob = " ".join(_norm(t) for t in texts if t)
    if not blob:
        return False
    for kw in ALTERNANCE_HINTS:
        if _norm(kw) in blob:
            return True
    return False


def fingerprint(source: str, external_id: str | None, url: str, title: str,
                company: str | None) -> str:
    """Stable hash used as UNIQUE key in DB.

    Prefer (source, external_id) when the source exposes a stable id.
    Otherwise fall back to normalized (url, title, company).
    """
    if external_id:
        seed = f"{source}::{external_id}"
    else:
        url_clean = re.sub(r"[?#].*$", "", url or "").rstrip("/")
        seed = f"{source}::{url_clean}::{_norm(title)}::{_norm(company or '')}"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]
