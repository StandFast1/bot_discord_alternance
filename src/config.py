"""Config strictly from process environment.

No .env file is ever read. On the VPS, systemd injects these via
EnvironmentFile loaded at service start by the GitHub Actions deploy step.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


def _opt(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    discord_token: str
    discord_guild_id: int
    discord_channel_id: int
    discord_user_id: int

    france_travail_client_id: str
    france_travail_client_secret: str

    grimp_cookie: str
    bluebox_cookie: str

    db_path: str
    excel_path: str
    scrape_interval_hours: int
    max_offers_per_cycle: int

    @classmethod
    def load(cls) -> "Config":
        return cls(
            discord_token=_req("DISCORD_TOKEN"),
            discord_guild_id=int(_req("DISCORD_GUILD_ID")),
            discord_channel_id=int(_req("DISCORD_CHANNEL_ID")),
            discord_user_id=int(_req("DISCORD_USER_ID")),
            france_travail_client_id=_opt("FRANCE_TRAVAIL_CLIENT_ID"),
            france_travail_client_secret=_opt("FRANCE_TRAVAIL_CLIENT_SECRET"),
            grimp_cookie=_opt("GRIMP_COOKIE"),
            bluebox_cookie=_opt("BLUEBOX_COOKIE"),
            db_path=_opt("DB_PATH", "/var/lib/alternance-bot/offers.db"),
            excel_path=_opt("EXCEL_PATH", "/var/lib/alternance-bot/candidatures.xlsx"),
            scrape_interval_hours=_int("SCRAPE_INTERVAL_HOURS", 2),
            max_offers_per_cycle=_int("MAX_OFFERS_PER_CYCLE", 30),
        )
