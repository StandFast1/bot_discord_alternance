"""Discord client + slash commands."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands

from .config import Config
from .db import Database
from .excel import ExcelExporter
from .notifier import OfferView, STATE_LABELS, build_embed


log = logging.getLogger(__name__)


class AlternanceBot(discord.Client):
    def __init__(self, cfg: Config, db: Database, excel: ExcelExporter):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.cfg = cfg
        self.db = db
        self.excel = excel
        self.tree = app_commands.CommandTree(self)
        self._channel: Optional[discord.TextChannel] = None
        self._on_ready_extra = None
        self._on_ready_fired = False
        self._register_commands()

    def set_on_ready(self, coro_factory) -> None:
        """Inject a coroutine factory invoked once the bot is fully ready."""
        self._on_ready_extra = coro_factory

    async def setup_hook(self) -> None:
        # Re-attach the persistent view so buttons keep working after restart
        self.add_view(OfferView(self.db, self.excel))
        guild = discord.Object(id=self.cfg.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, self.user.id if self.user else None)
        self._channel = self.get_channel(self.cfg.discord_channel_id)
        if not isinstance(self._channel, discord.TextChannel):
            try:
                self._channel = await self.fetch_channel(self.cfg.discord_channel_id)
            except discord.DiscordException as e:
                log.exception("cannot resolve channel: %s", e)
                return
        if self._on_ready_extra and not self._on_ready_fired:
            self._on_ready_fired = True
            asyncio.create_task(self._on_ready_extra())

    async def post_offer(self, offer_id: int) -> None:
        if not self._channel:
            log.warning("post_offer called before channel ready")
            return
        row = await self.db.get(offer_id)
        if not row:
            return
        embed = build_embed(row)
        view = OfferView(self.db, self.excel)
        content = f"<@{self.cfg.discord_user_id}> nouvelle offre"
        try:
            msg = await self._channel.send(content=content, embed=embed, view=view)
        except discord.DiscordException as e:
            log.exception("failed to post offer %d: %s", offer_id, e)
            return
        await self.db.set_message_id(offer_id, msg.id)

    # ---- slash commands ----

    def _register_commands(self) -> None:
        tree = self.tree

        @tree.command(name="stats",
                      description="Statistiques globales et du jour")
        async def stats(interaction: discord.Interaction):
            total = await self.db.stats()
            today = await self.db.stats_today()
            by_source = await self.db.stats_by_source()
            sent_history = await self.db.sent_per_day(days=7)

            embed = discord.Embed(
                title="📊 Statistiques candidatures",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Aujourd'hui",
                value="\n".join(
                    f"**{STATE_LABELS[s]}**: {today.get(s, 0)}"
                    for s in ("todo", "sent", "rejected", "ignored")
                ),
                inline=True,
            )
            embed.add_field(
                name="Total",
                value="\n".join(
                    f"**{STATE_LABELS[s]}**: {total.get(s, 0)}"
                    for s in ("new", "todo", "sent", "rejected", "ignored")
                ),
                inline=True,
            )

            sent_today = today.get("sent", 0)
            goal = 20
            filled = min(sent_today, goal)
            bar = "🟩" * filled + "⬜" * max(0, goal - filled)
            if sent_today > goal:
                bar += f" +{sent_today - goal}"
            embed.add_field(
                name=f"🎯 Objectif quotidien {sent_today}/{goal}",
                value=bar,
                inline=False,
            )

            # Per-source breakdown: total found / sent / rejected
            if by_source:
                src_lines = []
                for src in sorted(by_source.keys()):
                    s = by_source[src]
                    total_src = sum(s.values())
                    src_lines.append(
                        f"**{src}** · {total_src} trouv. · "
                        f"{s.get('sent', 0)} env. · {s.get('rejected', 0)} ref."
                    )
                embed.add_field(
                    name="Par source",
                    value="\n".join(src_lines),
                    inline=False,
                )

            # Response rate (out of sent applications)
            sent_total = total.get("sent", 0)
            rejected_total = total.get("rejected", 0)
            if sent_total > 0:
                rate = (rejected_total / sent_total) * 100
                embed.add_field(
                    name="Taux de refus (sur envoyées)",
                    value=f"{rejected_total}/{sent_total} = {rate:.1f}%",
                    inline=True,
                )

            # 7-day sent chart (ASCII)
            if sent_history:
                from datetime import date, timedelta
                today_d = date.today()
                full_history = []
                hist_dict = dict(sent_history)
                for i in range(6, -1, -1):
                    d = (today_d - timedelta(days=i)).isoformat()
                    full_history.append((d, hist_dict.get(d, 0)))
                max_n = max((n for _, n in full_history), default=1) or 1
                bar_lines = []
                for d, n in full_history:
                    blocks = "▇" * int((n / max_n) * 10) if n else ""
                    bar_lines.append(f"`{d[5:]}` {blocks} {n}")
                embed.add_field(
                    name="📈 Envoyées 7 derniers jours",
                    value="\n".join(bar_lines) or "—",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="todo",
                      description="Lister les offres marquées 'À faire'")
        async def todo_cmd(interaction: discord.Interaction):
            rows = await self.db.list_by_state("todo", limit=20)
            if not rows:
                await interaction.response.send_message(
                    "Aucune offre 'À faire'.", ephemeral=True
                )
                return
            lines = []
            for r in rows:
                title = (r["title"] or "(sans titre)")[:80]
                company = r["company"] or "—"
                lines.append(f"• [{title}]({r['url']}) — {company}")
            embed = discord.Embed(
                title=f"À faire ({len(rows)})",
                description="\n".join(lines),
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="new",
                      description="Lister les offres nouvelles non triées")
        async def new_cmd(interaction: discord.Interaction):
            rows = await self.db.list_by_state("new", limit=20)
            if not rows:
                await interaction.response.send_message(
                    "Aucune nouvelle offre en attente.", ephemeral=True
                )
                return
            lines = [
                f"• [{(r['title'] or '?')[:80]}]({r['url']}) — {r['company'] or '—'}"
                for r in rows
            ]
            embed = discord.Embed(
                title=f"Nouvelles ({len(rows)})",
                description="\n".join(lines),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="search",
                      description="Rechercher dans les offres collectées")
        @app_commands.describe(query="Mot-clé à chercher (titre, entreprise, description)")
        async def search_cmd(interaction: discord.Interaction, query: str):
            rows = await self.db.search(query, limit=15)
            if not rows:
                await interaction.response.send_message(
                    f"Aucun résultat pour `{query}`.", ephemeral=True
                )
                return
            lines = [
                f"• [{(r['title'] or '?')[:80]}]({r['url']}) — "
                f"{r['company'] or '—'} _{STATE_LABELS.get(r['state'], r['state'])}_"
                for r in rows
            ]
            embed = discord.Embed(
                title=f"Recherche: {query} ({len(rows)})",
                description="\n".join(lines),
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        self._scrape_callback = None

        @tree.command(name="scrape",
                      description="Lance un cycle de scraping immédiat")
        async def scrape_cmd(interaction: discord.Interaction):
            if not self._scrape_callback:
                await interaction.response.send_message(
                    "Scraper non initialisé.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "Cycle de scraping lancé en arrière-plan.", ephemeral=True
            )
            asyncio.create_task(self._scrape_callback())

        @tree.command(name="excel",
                      description="Télécharger le fichier Excel à jour")
        async def excel_cmd(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await self.excel.rebuild()
            except Exception as e:
                log.exception("excel rebuild failed on /excel: %s", e)
                await interaction.followup.send(
                    f"Erreur génération xlsx: {e}", ephemeral=True
                )
                return
            path = self.excel.path
            if not path.exists():
                await interaction.followup.send(
                    "Fichier introuvable.", ephemeral=True
                )
                return
            try:
                await interaction.followup.send(
                    file=discord.File(str(path), filename=path.name),
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.followup.send(
                    f"Envoi impossible (fichier trop gros ?): {e}",
                    ephemeral=True,
                )

    def set_scrape_callback(self, coro_factory) -> None:
        self._scrape_callback = coro_factory
