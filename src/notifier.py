"""Discord embed formatting + posting logic."""
from __future__ import annotations

import logging
import sqlite3

import discord

from .db import Database


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

    def __init__(self, db: Database):
        super().__init__(timeout=None)
        self.db = db

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
