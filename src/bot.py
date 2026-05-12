"""Discord client + slash commands."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands

from .config import Config
from .db import Database
from .excel import ExcelExporter
from .notifier import (
    OfferView,
    ProspectView,
    PROSPECT_STATE_LABELS,
    STATE_LABELS,
    build_embed,
    build_prospect_embed,
)
from .prospects import ProspectFinder

if TYPE_CHECKING:
    from .scheduler import Scraper


log = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {s}s" if s else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}j {hours}h"


class AlternanceBot(discord.Client):
    def __init__(self, cfg: Config, db: Database, excel: ExcelExporter,
                 prospects: ProspectFinder):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.cfg = cfg
        self.db = db
        self.excel = excel
        self.prospects = prospects
        self.tree = app_commands.CommandTree(self)
        self._channel: Optional[discord.TextChannel] = None
        self._on_ready_extra = None
        self._on_ready_fired = False
        self._scraper: Optional["Scraper"] = None
        self._start_time: datetime = datetime.now(timezone.utc)
        self._register_commands()

    def set_scraper(self, scraper: "Scraper") -> None:
        self._scraper = scraper

    def set_on_ready(self, coro_factory) -> None:
        """Inject a coroutine factory invoked once the bot is fully ready."""
        self._on_ready_extra = coro_factory

    async def setup_hook(self) -> None:
        # Re-attach the persistent views so buttons keep working after restart
        self.add_view(OfferView(self.db, self.excel))
        self.add_view(ProspectView(self.prospects))
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

    async def post_prospect(self, prospect_id: int) -> None:
        if not self._channel:
            return
        row = await self.prospects.get(prospect_id)
        if not row:
            return
        embed = build_prospect_embed(row)
        view = ProspectView(self.prospects)
        try:
            msg = await self._channel.send(embed=embed, view=view)
        except discord.DiscordException as e:
            log.exception("failed to post prospect %d: %s", prospect_id, e)
            return
        await self.prospects.set_message_id(prospect_id, msg.id)

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
                      description="Lance un cycle de scraping immédiat et attend le résultat")
        async def scrape_cmd(interaction: discord.Interaction):
            if not self._scraper:
                await interaction.response.send_message(
                    "Scraper non initialisé.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                result = await self._scraper.run_once()
            except Exception as e:
                log.exception("manual scrape failed: %s", e)
                await interaction.followup.send(
                    f"❌ Scrape échoué : {e}", ephemeral=True
                )
                return
            if result.skipped:
                await interaction.followup.send(
                    "⏳ Un cycle de scrape est déjà en cours, réessaie plus tard.",
                    ephemeral=True,
                )
                return
            embed = discord.Embed(
                title="🔄 Cycle de scraping terminé",
                description=f"**{result.inserted_total}** nouvelles offres ajoutées.",
                color=discord.Color.green() if result.inserted_total
                      else discord.Color.greyple(),
            )
            lines = []
            for src, stats in sorted(result.per_source.items()):
                lines.append(
                    f"**{src}** · {stats['fetched']} trouvées · "
                    f"{stats['inserted']} nouvelles"
                )
            if lines:
                embed.add_field(name="Par source", value="\n".join(lines),
                                inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

        @tree.command(name="status",
                      description="État du bot : uptime, dernier et prochain scrape")
        async def status_cmd(interaction: discord.Interaction):
            now = datetime.now(timezone.utc)
            uptime = now - self._start_time
            embed = discord.Embed(
                title="🟢 Bot status",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Uptime",
                value=_format_duration(uptime.total_seconds()),
                inline=True,
            )
            embed.add_field(
                name="Intervalle scrape",
                value=f"{self.cfg.scrape_interval_hours}h",
                inline=True,
            )
            if self._scraper:
                last = self._scraper.last_run_at
                if last:
                    elapsed = (now - last).total_seconds()
                    n = (self._scraper.last_run_result.inserted_total
                         if self._scraper.last_run_result else 0)
                    embed.add_field(
                        name="Dernier scrape",
                        value=f"il y a {_format_duration(elapsed)} "
                              f"({n} nouvelle(s))",
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Dernier scrape",
                        value="jamais (cycle en cours ou démarrage récent)",
                        inline=False,
                    )
                nxt = self._scraper.next_run_at
                if nxt:
                    remaining = (nxt - now).total_seconds()
                    if remaining < 0:
                        remaining_str = "imminent"
                    else:
                        remaining_str = f"dans {_format_duration(remaining)}"
                    embed.add_field(
                        name="Prochain scrape",
                        value=remaining_str,
                        inline=False,
                    )
            await interaction.response.send_message(embed=embed, ephemeral=True)

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

        @tree.command(
            name="prospect",
            description="Cherche de nouvelles entreprises IDF à démarcher",
        )
        @app_commands.describe(
            limit="Nombre max de nouveaux prospects à trouver (défaut 10, max 30)"
        )
        async def prospect_cmd(interaction: discord.Interaction,
                               limit: int = 10):
            limit = max(1, min(30, limit))
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                new_ids = await self.prospects.find_new(limit=limit)
            except Exception as e:
                log.exception("prospect search failed: %s", e)
                await interaction.followup.send(
                    f"❌ Recherche échouée : {e}", ephemeral=True
                )
                return
            if not new_ids:
                await interaction.followup.send(
                    "Aucun nouveau prospect trouvé "
                    "(ils sont peut-être déjà tous en base ou dans tes offres).",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"🎯 {len(new_ids)} nouveaux prospects trouvés, je les poste...",
                ephemeral=True,
            )
            for pid in new_ids:
                await self.post_prospect(pid)
                await asyncio.sleep(0.7)

        @tree.command(
            name="prospects",
            description="Liste tes prospects par état",
        )
        @app_commands.describe(
            state="État à filtrer (défaut: to_contact)"
        )
        @app_commands.choices(state=[
            app_commands.Choice(name=label, value=value)
            for value, label in PROSPECT_STATE_LABELS.items()
        ])
        async def prospects_cmd(
            interaction: discord.Interaction,
            state: str = "to_contact",
        ):
            rows = await self.prospects.list_by_state(state, limit=25)
            if not rows:
                await interaction.response.send_message(
                    f"Aucun prospect en état **{PROSPECT_STATE_LABELS.get(state, state)}**.",
                    ephemeral=True,
                )
                return
            lines = []
            for r in rows:
                loc = " ".join(p for p in (r["postal_code"], r["city"]) if p)
                lines.append(
                    f"• **{(r['name'] or '?')[:50]}** — {loc or '—'} "
                    f"({r['headcount'] or '?'} sal.)"
                )
            embed = discord.Embed(
                title=f"Prospects · {PROSPECT_STATE_LABELS.get(state, state)} "
                      f"({len(rows)})",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(
            name="prospect_stats",
            description="Statistiques de prospection",
        )
        async def prospect_stats_cmd(interaction: discord.Interaction):
            stats = await self.prospects.stats()
            total = sum(stats.values())
            embed = discord.Embed(
                title="🎯 Prospection",
                description=f"**{total}** entreprises en base",
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Pipeline",
                value="\n".join(
                    f"**{PROSPECT_STATE_LABELS[k]}**: {v}"
                    for k, v in stats.items()
                ),
                inline=False,
            )
            contacted = stats.get("contacted", 0) + stats.get("responded", 0) \
                + stats.get("interview", 0)
            responded = stats.get("responded", 0) + stats.get("interview", 0)
            if contacted > 0:
                rate = (responded / contacted) * 100
                embed.add_field(
                    name="Taux de réponse",
                    value=f"{responded}/{contacted} = {rate:.1f}%",
                    inline=True,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="help",
                      description="Liste toutes les commandes du bot")
        async def help_cmd(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🤖 Commandes du bot alternance",
                description="Toutes les réponses sont **ephemeral** "
                            "(visibles que par toi).",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="📊 Suivi",
                value=(
                    "`/stats` — tableau de bord : aujourd'hui, total, "
                    "par source, taux de refus, graphe 7 jours\n"
                    "`/status` — uptime, dernier et prochain scrape\n"
                    "`/todo` — offres marquées **À faire**\n"
                    "`/new` — offres nouvelles non triées\n"
                    "`/search <mot>` — chercher dans titre/entreprise/description"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔄 Scraping",
                value=(
                    f"`/scrape` — lance un cycle immédiat "
                    f"(auto toutes les {self.cfg.scrape_interval_hours}h)\n"
                ),
                inline=False,
            )
            embed.add_field(
                name="📑 Export",
                value=(
                    "`/excel` — télécharger le fichier xlsx à jour "
                    "(rebuild auto à chaque changement)"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎯 Prospection (démarchage entreprises)",
                value=(
                    "`/prospect [limit]` — cherche entreprises IDF cyber/IT "
                    "non déjà dans tes offres\n"
                    "`/prospects [state]` — liste tes prospects par état\n"
                    "`/prospect_stats` — pipeline et taux de réponse"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎛️ Boutons sur chaque offre",
                value=(
                    "📌 **À faire** — à candidater bientôt\n"
                    "✅ **Envoyée** — candidature envoyée "
                    "(compte pour l'objectif quotidien)\n"
                    "❌ **Refus** — réponse négative\n"
                    "🗑️ **Ignorer** — pas pertinente, à oublier"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎛️ Boutons sur chaque prospect",
                value=(
                    "🎯 **À démarcher** · 📨 **Contacté** · "
                    "💬 **A répondu** · 🤝 **Entretien** · ❌ **Pas intéressé**"
                ),
                inline=False,
            )
            embed.set_footer(
                text=f"Objectif : 20 candidatures envoyées par jour · "
                     f"Interval scrape : {self.cfg.scrape_interval_hours}h"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    def set_scrape_callback(self, coro_factory) -> None:
        self._scrape_callback = coro_factory
