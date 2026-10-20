from __future__ import annotations

import asyncio
import difflib
import json
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

# BallsDex changed where models live between versions; support both.
try:
    from bd_models.models import BallInstance, Player, balls  # newer (Django) versions

    DJANGO = True
except ImportError:  # pragma: no cover
    from ballsdex.core.models import BallInstance, Player, balls  # older (Tortoise) versions

    DJANGO = False

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

# ---------------- CONFIG ----------------
TITLE = "The Codex Archives"
PER_PAGE = 7
TIMEOUT = 600  # seconds of inactivity before the menu locks (keep under 15 min)
TIP_INTERVAL = 10  # seconds between tip changes

SECRET_BALL_ID: int | None = None  # None = random ball (weighted by rarity); or a fixed ball id
CLAIMS_FILE = Path(__file__).parent / "secret_claims.json"  # who already used the secret button

EMBED_COLOR = 0x5865F2  # list view
SEARCH_COLOR = 0x57F287  # search results
DETAIL_COLOR = 0xFEE75C  # object card
ERROR_COLOR = 0xED4245

TIPS = [
    "The lower the tier/number, the rarer it is, dont forget!",
    "Dont know an object? Use the Who is This? Button!",
    "Codex is a sibling i think idk lmao",
    "happy birthday to rarity",
    "Hey, its me, its rarity!",
    "Characters not in the dex yet? Submit at the official server!",
    "watchraritieslive.tv",
    "Thanks for using the dex!",
    "astatine is eat my kid",
    "Hand over your kneecaps before it's too late...",
    "Labubus dubai matcha chocolate Ohio,something is cooking in 3 days",
    "RASPBERRY PLASTIC TICKLE BEAR",
]

# ----------------------------------------

@dataclass
class Entry:
    id: int
    name: str
    emoji: discord.Emoji | None
    rarity: float
    ability: str
    description: str
    attack: int | None
    health: int | None

    @property
    def emoji_str(self) -> str:
        return str(self.emoji) if self.emoji else ""


def load_entries(bot: "BallsDexBot") -> list[Entry]:
    """All enabled objects sorted by rarity (lowest = rarest), then name."""
    entries: list[Entry] = []
    for ball in balls.values():
        if not getattr(ball, "enabled", True):
            continue
        entries.append(
            Entry(
                id=ball.pk,
                name=ball.country,
                emoji=bot.get_emoji(ball.emoji_id),
                rarity=ball.rarity,
                ability=(getattr(ball, "capacity_name", "") or "").strip(),
                description=(getattr(ball, "capacity_description", "") or "").strip(),
                attack=getattr(ball, "attack", None),
                health=getattr(ball, "health", None),
            )
        )
    entries.sort(key=lambda e: (e.rarity, e.name.lower()))
    return entries


def search_entries(entries: list[Entry], query: str) -> tuple[list[Entry], list[Entry]]:
    """Return (matches, suggestions). Suggestions are only filled when there are no matches."""
    q = query.lower().strip()
    starts = [e for e in entries if e.name.lower().startswith(q)]
    seen = {e.id for e in starts}
    contains = [e for e in entries if q in e.name.lower() and e.id not in seen]
    matches = starts + contains
    suggestions: list[Entry] = []
    if not matches:
        by_name = {e.name.lower(): e for e in entries}
        close = difflib.get_close_matches(q, list(by_name), n=10, cutoff=0.4)
        suggestions = [by_name[n] for n in close]
    return matches, suggestions


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ClaimStore:
    """Remembers which users already used the secret button (persisted to a JSON file)."""

    def __init__(self, path: Path = CLAIMS_FILE):
        self.path = path
        self.lock = asyncio.Lock()
        self.claimed: set[int] = set()
        try:
            self.claimed = {int(i) for i in json.loads(path.read_text(encoding="utf-8"))}
        except (FileNotFoundError, ValueError):
            pass

    def has(self, user_id: int) -> bool:
        return user_id in self.claimed

    def _save(self):
        self.path.write_text(json.dumps(sorted(self.claimed)), encoding="utf-8")

    async def try_claim(self, user_id: int) -> bool:
        """Atomically mark the user as claimed. Returns False if they already had."""
        async with self.lock:
            if user_id in self.claimed:
                return False
            self.claimed.add(user_id)
            try:
                self._save()
            except Exception:
                self.claimed.discard(user_id)
                raise
            return True

    async def release(self, user_id: int):
        async with self.lock:
            self.claimed.discard(user_id)
            try:
                self._save()
            except Exception:
                log.exception("could not save")


def pick_ball():
    pool = [b for b in balls.values() if getattr(b, "enabled", True)]
    if SECRET_BALL_ID is not None:
        fixed = next((b for b in pool if b.pk == SECRET_BALL_ID), None)
        if fixed:
            return fixed
    # BallsDex convention: higher rarity number = more common, so use it as the weight
    weights = [max(float(b.rarity), 0.0001) for b in pool]
    return random.choices(pool, weights=weights, k=1)[0]


async def give_ball(user_id: int, guild_id: int | None, ball):
    """Create a BallInstance for the user. Returns it."""
    atk = random.randint(-20, 20)
    hp = random.randint(-20, 20)
    kwargs = dict(
        ball=ball,
        attack_bonus=atk,
        health_bonus=hp,
        server_id=guild_id,
        spawned_time=datetime.now(timezone.utc),
    )
    if DJANGO:
        player, _ = await Player.objects.aget_or_create(discord_id=user_id)
        return await BallInstance.objects.acreate(player=player, **kwargs)
    player, _ = await Player.get_or_create(discord_id=user_id)
    return await BallInstance.create(player=player, **kwargs)


# ----------------------------------------------------------------- modals


class SearchModal(discord.ui.Modal, title="Search The Codex Archives"):
    query = discord.ui.TextInput(
        label="What are you looking for?",
        placeholder="e.g. Match, Stop Sign, Moon, Verity? (leave blank to reset)",
        required=False,
        max_length=100,
    )

    def __init__(self, view: "RarityView"):
        super().__init__()
        self.paginator = view

    async def on_submit(self, interaction: discord.Interaction):
        self.paginator.apply_search(self.query.value.strip())
        await self.paginator.update(interaction)


# ------------------------------------------------------------------- view


class RarityView(discord.ui.View):
    def __init__(
        self,
        bot: "BallsDexBot",
        author: discord.abc.User,
        entries: list[Entry],
        claims: ClaimStore,
    ):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.author = author
        self.claims = claims
        self.all = entries
        self.by_id = {e.id: e for e in entries}
        self.rank = {e.id: i + 1 for i, e in enumerate(entries)}

        self.entries = entries
        self.results_title: str | None = None  # set => showing search results
        self.results_query = ""
        self.suggestions: list[Entry] = []
        self.page = 0
        self.focus: Entry | None = None  # set => showing the object card

        self.tip_index = random.randrange(len(TIPS))
        self.message: discord.Message | None = None
        self._ticker: asyncio.Task | None = None
        self.refresh()

    # ----- state helpers
    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.entries) / PER_PAGE))

    def page_entries(self) -> list[Entry]:
        start = self.page * PER_PAGE
        return self.entries[start : start + PER_PAGE]

    def set_results(
        self, title: str | None, query: str, matches: list[Entry], suggestions: list[Entry]
    ):
        self.focus = None
        self.page = 0
        self.results_title = title
        self.results_query = query
        self.entries = matches
        self.suggestions = suggestions

    def apply_search(self, query: str):
        if not query:
            self.set_results(None, "", self.all, [])
            return
        matches, suggestions = search_entries(self.all, query)
        self.set_results(f"Results for “{short(query, 60)}”", query, matches, suggestions)

    async def update(self, interaction: discord.Interaction):
        self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    # ----- tips ticker
    @property
    def tip(self) -> str:
        return TIPS[self.tip_index % len(TIPS)]

    def start_ticker(self):
        self._ticker = asyncio.create_task(self._tick())

    def stop_ticker(self):
        if self._ticker and not self._ticker.done():
            self._ticker.cancel()

    async def _tick(self):
        try:
            while not self.is_finished():
                await asyncio.sleep(TIP_INTERVAL)
                if self.is_finished() or not self.message:
                    break
                self.tip_index = (self.tip_index + 1) % len(TIPS)
                try:
                    await self.message.edit(embed=self.embed(), view=self)
                except discord.HTTPException:
                    break
        except asyncio.CancelledError:
            pass

    def footer(self, prefix: str = "") -> str:
        return f"{prefix} {self.tip}"

    # ----- embeds
    def embed(self) -> discord.Embed:
        if self.focus:
            return self.detail_embed(self.focus)
        if self.results_title and not self.entries:
            return self.no_results_embed()
        return self.list_embed()

    def entry_line(self, e: Entry) -> str:
        return (
            f"**#{self.rank[e.id]} · {e.name}**\n"
            f"{e.emoji_str} Rarity: `{e.rarity:g}`"
        ).replace("\n ", "\n")

    def list_embed(self) -> discord.Embed:
        body = "\n\n".join(self.entry_line(e) for e in self.page_entries())
        page_text = f"Page {self.page + 1}/{self.total_pages}  •  "

        if self.results_title:
            count = len(self.entries)
            embed = discord.Embed(
                title=self.results_title,
                description=(
                    f"-# Found **{count}** object{'s' if count != 1 else ''} • "
                    f"pick one below to see its ability\n\n{body}"
                ),
                color=SEARCH_COLOR,
            )
        else:
            embed = discord.Embed(
                title=f"{TITLE}",
                description=f"-# Lower number = rarer • {len(self.all)} objects\n\n{body}",
                color=EMBED_COLOR,
            )
        embed.set_footer(text=self.footer(page_text))
        return embed

    def no_results_embed(self) -> discord.Embed:
        text = f"Nothing matched **{short(self.results_query, 60)}**."
        if self.suggestions:
            did_you_mean = "\n".join(
                f"• {e.emoji_str} **{e.name}**" for e in self.suggestions[:10]
            )
            text += f"\n\n**Did you mean this?**\n{did_you_mean}"
        else:
            text += "\n\nTry a shorter or different spelling."
        embed = discord.Embed(title="🔎 No results, sorry man.", description=text, color=ERROR_COLOR)
        embed.set_footer(text=self.footer())
        return embed

    def detail_embed(self, e: Entry) -> discord.Embed:
        embed = discord.Embed(
            title=f"{e.emoji_str} {e.name}".strip(),
            color=DETAIL_COLOR,
        )
        embed.add_field(name="Rarity", value=f"`{e.rarity:g}`", inline=True)
        embed.add_field(
            name="Tier", value=f"`#{self.rank[e.id]} / {len(self.all)}`", inline=True
        )
        if e.attack is not None and e.health is not None:
            embed.add_field(
                name="Stats", value=f"`{e.attack} ATK • {e.health} HP`", inline=True
            )

        ability_name = e.ability or "Melody wheres the name of this."
        ability_desc = e.description or "THe fuck? Wheres the ability desc Melody???"
        embed.add_field(
            name="\u200b",
            value=f"### ✨ {short(ability_name, 200)}\n>>> {short(ability_desc, 900)}",
            inline=False,
        )
        if e.emoji:
            embed.set_thumbnail(url=e.emoji.url)
        embed.set_footer(text=self.footer())
        return embed

    # ----- components
    def refresh(self):
        """Rebuild the buttons/selects for the current state."""
        self.clear_items()
        if self.focus:
            self._add_button("Back to list", self._back, discord.ButtonStyle.primary, "↩️")
            self._add_button("Search", self._search, discord.ButtonStyle.secondary, "🔎")
            self._add_button("Quit", self._quit, discord.ButtonStyle.danger)
            return

        # Row 0: inspect dropdown (doubles as the suggestions picker)
        options_src = self.page_entries() if self.entries else self.suggestions[:25]
        if options_src:
            select = discord.ui.Select(
                placeholder=(
                    "Inspect"
                    if self.entries
                    else "Did you mean this?"
                ),
                options=[
                    discord.SelectOption(
                        label=short(e.name, 100),
                        value=str(e.id),
                        description=short(
                            f"{e.ability + ' • ' if e.ability else ''}Rarity {e.rarity:g}", 100
                        ),
                        emoji=e.emoji,
                    )
                    for e in options_src
                ],
                row=0,
            )
            select.callback = self._select
            self.add_item(select)

        # Row 1: search / clear
        self._add_button("Search", self._search, discord.ButtonStyle.secondary, "🔎", row=1)
        if self.results_title:
            self._add_button("Clear", self._clear, discord.ButtonStyle.secondary, "✖️", row=1)

        # Row 2: navigation
        no_prev = self.page <= 0
        no_next = self.page >= self.total_pages - 1
        self._add_button("≪", self._first, discord.ButtonStyle.secondary, row=2, disabled=no_prev)
        self._add_button("Back", self._prev, discord.ButtonStyle.primary, row=2, disabled=no_prev)
        self._add_button("Next", self._next, discord.ButtonStyle.primary, row=2, disabled=no_next)
        self._add_button("≫", self._last, discord.ButtonStyle.secondary, row=2, disabled=no_next)
        self._add_button("Quit", self._quit, discord.ButtonStyle.danger, row=2)

        # Row 4: the secret button (disappears once this user has used it)
        if not self.claims.has(self.author.id):
            self._add_button("·", self._secret, discord.ButtonStyle.secondary, row=4)

    def _add_button(self, label, callback, style, emoji=None, row=None, disabled=False):
        button = discord.ui.Button(
            label=label, style=style, emoji=emoji, row=row, disabled=disabled
        )
        button.callback = callback
        self.add_item(button)

    # ----- callbacks
    async def _select(self, interaction: discord.Interaction):
        chosen = int(interaction.data["values"][0])  # type: ignore
        self.focus = self.by_id[chosen]
        await self.update(interaction)

    async def _back(self, interaction: discord.Interaction):
        self.focus = None
        await self.update(interaction)

    async def _search(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self))

    async def _clear(self, interaction: discord.Interaction):
        self.apply_search("")
        await self.update(interaction)

    async def _first(self, interaction: discord.Interaction):
        self.page = 0
        await self.update(interaction)

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.update(interaction)

    async def _last(self, interaction: discord.Interaction):
        self.page = self.total_pages - 1
        await self.update(interaction)

    async def _secret(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        claimed = False
        try:
            if not await self.claims.try_claim(user_id):
                self.refresh()
                await interaction.edit_original_response(embed=self.embed(), view=self)
                await interaction.followup.send(
                    "FUCK YOU THIS IS ONETIME ONLY YOU HEAR ME", ephemeral=True
                )
                return
            claimed = True
            ball = pick_ball()
            instance = await give_ball(user_id, interaction.guild_id, ball)
        except Exception:
            log.exception("Secret failed")
            if claimed:
                await self.claims.release(user_id)
            await interaction.followup.send(
                "Hm. Error occured, DM kaboomtsarbobm pwease.",
                ephemeral=True,
            )
            return

        # Remove the button from the menu, then reveal the prize privately
        self.refresh()
        await interaction.edit_original_response(embed=self.embed(), view=self)

        entry = self.by_id.get(ball.pk)
        atk = getattr(instance, "attack_bonus", 0)
        hp = getattr(instance, "health_bonus", 0)
        prize = discord.Embed(
            title="Wow you found le hidden button",
            description=(
                f"{entry.emoji_str if entry else ''} **{ball.country}** is now yours.\n"
                f"Rarity `{ball.rarity:g}`\n"
                f"`ATK {atk:+d}%` • `HP {hp:+d}%`\n\n"
                "-# THIS IS ONETIME"
            ),
            color=DETAIL_COLOR,
        )
        if entry and entry.emoji:
            prize.set_thumbnail(url=entry.emoji.url)
        await interaction.followup.send(embed=prize, ephemeral=True)

    async def _quit(self, interaction: discord.Interaction):
        self.stop()
        self.stop_ticker()
        await interaction.response.defer()
        await interaction.delete_original_response()

    # ----- lifecycle
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Who are YOU??", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.stop_ticker()
        for item in self.children:
            item.disabled = True  # type: ignore
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# -------------------------------------------------------------------- cog


class Rarity(commands.Cog):
    """Paginated rarity list with search, suggestions, ability cards and rotating tips."""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.claims = ClaimStore()

    @app_commands.command(name="rarity", description="Show the rarity list of every object")
    @app_commands.describe(name="Jump straight to an object (suggestions appear as you type)")
    async def rarity(self, interaction: discord.Interaction, name: str | None = None):
        entries = load_entries(self.bot)
        view = RarityView(self.bot, interaction.user, entries, self.claims)

        if name:
            exact = next((e for e in entries if e.name.lower() == name.lower()), None)
            if exact:
                view.focus = exact
            else:
                view.apply_search(name)
            view.refresh()

        await interaction.response.send_message(embed=view.embed(), view=view)
        view.message = await interaction.original_response()
        view.start_ticker()

    @rarity.autocomplete("name")
    async def name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        entries = load_entries(self.bot)
        if current.strip():
            matches, suggestions = search_entries(entries, current)
            results = matches or suggestions
        else:
            results = entries
        return [
            app_commands.Choice(
                name=short(f"{e.name} • Rarity {e.rarity:g}", 100),
                value=short(e.name, 100),
            )
            for e in results[:25]
        ]