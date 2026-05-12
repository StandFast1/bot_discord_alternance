"""Discord embed formatting + posting logic."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Optional

import discord

from .db import Database
from .excel import ExcelExporter
from .prospects import ProspectFinder


log = logging.getLogger(__name__)


STATE_COLORS = {
    "new": discord.Color.blurple(),
    "todo": discord.Color.orange(),
    "sent": discord.Color.green(),
    "rejected": discord.Color.red(),
    "ignored": discord.Color.dark_grey(),
}

STATE_LABELS = {
    "new": "Nouveau",
    "todo": "À faire",
    "sent": "Envoyée",
    "rejected": "Refusée",
    "ignored": "Ignorée",
}

SOURCE_LABELS = {
    "france_travail": "France Travail",
    "hellowork": "HelloWork",
    "wttj": "Welcome to the Jungle",
    "apec": "APEC",
}


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_embed(row: sqlite3.Row) -> discord.Embed:
    state = row["state"]
    embed = discord.Embed(
        title=_truncate(row["title"], 240) or "(sans titre)",
        url=row["url"],
        color=STATE_COLORS.get(state, discord.Color.blurple()),
        description=_truncate(row["description"], 400) or None,
    )
    if row["company"]:
        embed.add_field(name="Entreprise", value=_truncate(row["company"], 100),
                        inline=True)
    if row["location"]:
        embed.add_field(name="Lieu", value=_truncate(row["location"], 100),
                        inline=True)
    if row["contract"]:
        embed.add_field(name="Contrat", value=_truncate(row["contract"], 60),
                        inline=True)
    embed.set_footer(
        text=f"{SOURCE_LABELS.get(row['source'], row['source'])} · "
             f"État: {STATE_LABELS.get(state, state)}"
    )
    return embed


class OfferView(discord.ui.View):
    """Persistent view: buttons survive bot restarts via custom_id."""

    def __init__(self, db: Database, excel: Optional[ExcelExporter] = None):
        super().__init__(timeout=None)
        self.db = db
        self.excel = excel

    async def _update(self, interaction: discord.Interaction, state: str) -> None:
        if not interaction.message:
            await interaction.response.send_message(
                "Message introuvable.", ephemeral=True
            )
            return
        row = await self.db.get_by_message(interaction.message.id)
        if not row:
            await interaction.response.send_message(
                "Offre introuvable en base.", ephemeral=True
            )
            return
        await self.db.set_state(row["id"], state)
        new_row = await self.db.get(row["id"])
        embed = build_embed(new_row)
        await interaction.response.edit_message(embed=embed, view=self)
        log.info("offer %d -> %s by %s", row["id"], state, interaction.user)
        if self.excel:
            asyncio.create_task(self.excel.rebuild_safe())

    @discord.ui.button(
        label="À faire", style=discord.ButtonStyle.primary,
        custom_id="offer:todo", emoji="📌",
    )
    async def todo(self, interaction: discord.Interaction,
                   _button: discord.ui.Button):
        await self._update(interaction, "todo")

    @discord.ui.button(
        label="Envoyée", style=discord.ButtonStyle.success,
        custom_id="offer:sent", emoji="✅",
    )
    async def sent(self, interaction: discord.Interaction,
                   _button: discord.ui.Button):
        await self._update(interaction, "sent")

    @discord.ui.button(
        label="Refus", style=discord.ButtonStyle.danger,
        custom_id="offer:rejected", emoji="❌",
    )
    async def reject(self, interaction: discord.Interaction,
                     _button: discord.ui.Button):
        await self._update(interaction, "rejected")

    @discord.ui.button(
        label="Ignorer", style=discord.ButtonStyle.secondary,
        custom_id="offer:ignored", emoji="🗑️",
    )
    async def ignore(self, interaction: discord.Interaction,
                     _button: discord.ui.Button):
        await self._update(interaction, "ignored")


# ---------- Prospects ----------

PROSPECT_STATE_LABELS = {
    "new":            "Nouveau",
    "to_contact":     "À démarcher",
    "contacted":      "Contacté",
    "responded":      "A répondu",
    "interview":      "Entretien obtenu",
    "not_interested": "Pas intéressé",
}

PROSPECT_STATE_COLORS = {
    "new":            discord.Color.blurple(),
    "to_contact":     discord.Color.orange(),
    "contacted":      discord.Color.gold(),
    "responded":      discord.Color.teal(),
    "interview":      discord.Color.green(),
    "not_interested": discord.Color.dark_grey(),
}


def build_prospect_embed(row) -> discord.Embed:
    state = row["state"]
    title = _truncate(row["name"], 240) or "(sans nom)"
    embed = discord.Embed(
        title=f"🏢 {title}",
        color=PROSPECT_STATE_COLORS.get(state, discord.Color.blurple()),
    )
    if row["naf_label"]:
        embed.add_field(
            name="Activité",
            value=_truncate(row["naf_label"], 100),
            inline=False,
        )
    if row["headcount"]:
        embed.add_field(name="Effectif", value=row["headcount"], inline=True)
    location_parts = []
    if row["postal_code"]:
        location_parts.append(row["postal_code"])
    if row["city"]:
        location_parts.append(row["city"])
    if location_parts:
        embed.add_field(name="Lieu", value=" ".join(location_parts), inline=True)
    if row["siret"]:
        embed.add_field(name="SIRET", value=row["siret"], inline=True)

    links = []
    if row["website"]:
        links.append(f"[Site web]({row['website']})")
    if row["linkedin_search"]:
        links.append(f"[🔗 Trouver RH sur LinkedIn]({row['linkedin_search']})")
    # Lien annuaire entreprises pour fact-check
    if row["siret"]:
        links.append(
            f"[Annuaire INSEE](https://annuaire-entreprises.data.gouv.fr/"
            f"entreprise/{row['siret']})"
        )
    if links:
        embed.add_field(name="Liens", value="\n".join(links), inline=False)

    embed.set_footer(
        text=f"État: {PROSPECT_STATE_LABELS.get(state, state)}"
    )
    return embed


class ProspectView(discord.ui.View):
    """Persistent view for prospect cards. custom_id-based for restart resilience."""

    def __init__(self, prospects: ProspectFinder):
        super().__init__(timeout=None)
        self.prospects = prospects

    async def _update(self, interaction: discord.Interaction, state: str) -> None:
        if not interaction.message:
            await interaction.response.send_message(
                "Message introuvable.", ephemeral=True
            )
            return
        row = await self.prospects.get_by_message(interaction.message.id)
        if not row:
            await interaction.response.send_message(
                "Prospect introuvable en base.", ephemeral=True
            )
            return
        await self.prospects.set_state(row["id"], state)
        new_row = await self.prospects.get(row["id"])
        embed = build_prospect_embed(new_row)
        await interaction.response.edit_message(embed=embed, view=self)
        log.info("prospect %d -> %s by %s", row["id"], state, interaction.user)

    @discord.ui.button(
        label="À démarcher", style=discord.ButtonStyle.primary,
        custom_id="prospect:to_contact", emoji="🎯",
    )
    async def to_contact(self, interaction: discord.Interaction,
                          _button: discord.ui.Button):
        await self._update(interaction, "to_contact")

    @discord.ui.button(
        label="Contacté", style=discord.ButtonStyle.secondary,
        custom_id="prospect:contacted", emoji="📨",
    )
    async def contacted(self, interaction: discord.Interaction,
                         _button: discord.ui.Button):
        await self._update(interaction, "contacted")

    @discord.ui.button(
        label="A répondu", style=discord.ButtonStyle.success,
        custom_id="prospect:responded", emoji="💬",
    )
    async def responded(self, interaction: discord.Interaction,
                         _button: discord.ui.Button):
        await self._update(interaction, "responded")

    @discord.ui.button(
        label="Entretien", style=discord.ButtonStyle.success,
        custom_id="prospect:interview", emoji="🤝",
    )
    async def interview(self, interaction: discord.Interaction,
                         _button: discord.ui.Button):
        await self._update(interaction, "interview")

    @discord.ui.button(
        label="Pas intéressé", style=discord.ButtonStyle.danger,
        custom_id="prospect:not_interested", emoji="❌",
    )
    async def not_interested(self, interaction: discord.Interaction,
                              _button: discord.ui.Button):
        await self._update(interaction, "not_interested")
