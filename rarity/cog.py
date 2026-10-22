from __future__ import annotations

import asyncio
import base64
import difflib
import io
import json
import logging
import math
import os
import re
import random
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

try:
    from bd_models.models import transform_media
except ImportError:  # pragma: no cover

    def transform_media(path: str) -> str:
        return path


log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

# ---------------- CONFIG ----------------
TITLE = "The Codex Archives"
PER_PAGE = 7
TIMEOUT = 600  # seconds of inactivity before the menu locks (keep under 15 min)
TIP_INTERVAL = 10  # seconds between tip changes
CLICK_COOLDOWN = 0.6  # clicks faster than this are ignored (stops button spam)
CACHE_SECONDS = 30  # how long the object list is reused between commands

RARITY_MIN = 1.0  # rarest value on this bot
RARITY_MAX = 100.0  # most common value on this bot
# Where your bot serves /media/ (e.g. "https://admin.example.com"). Empty = NO card link is shown.
# You can also set it without editing this file: environment variable RARITY_CARD_URL_BASE
CARD_URL_BASE = os.environ.get("RARITY_CARD_URL_BASE", "")
TIPS_AUTOPLAY = True  # False = tips start paused and the Tips button "plays" them
DIVIDER = "✦━━━━━━━━━❖━━━━━━━━━✦"
# colour stops from the rarest page to the most common page (r, g, b)
GRADIENT = [(255, 193, 7), (255, 112, 67), (236, 64, 122), (126, 87, 194), (66, 133, 244), (149, 165, 166)]
SPECTRUM = ["🟨", "🟧", "🟧", "🟥", "🟥", "🟪", "🟪", "🟦", "🟦", "⬜"]  # same fade, as a strip
SORT_LABELS = ["Sort: Default", "Sort: Most common", "Sort: A–Z"]

# type one of these in Search and the reply shows under the results
EASTER = {
    "verity": "ask me anything",
    "nintendo": "nintendo is epic",
    "codex": "brother you know the rarity",
    "codexy": "brother you know the rarity",
    "spongebob": "IM SPONGEBOB",
}

SECRET_PHRASE_SKIP_ZERO = True  # the secret phrase skips objects with rarity 0.0 or lower
SECRET_PHRASE = "asdfghjklcodex4life"  # type this in Search to get the rarest object (once per user). "" = off
PHRASE_CLAIMS_FILE = Path(__file__).parent / "secret_phrase_claims.json"

SECRET_BALL_ID: int | None = None  # None = random ball (weighted by rarity); or a fixed ball id
CLAIMS_FILE = Path(__file__).parent / "secret_claims.json"  # who already used the secret button

FONT_DEFAULT = "default"  # default font for new users: default / coolvetica / vcr / fe / off
HIDE_ZERO_DEFAULT = False  # True = hide objects with rarity 0.0 or lower until the user shows them
PREFS_FILE = Path(__file__).parent / "rarity_prefs.json"  # remembers each user's font + hide choice

LAYOUT_DEFAULT = "cards"
# name -> (label, objects per page, short description shown in the layout picker)
LAYOUTS = {
    "cards": ("Cards", 7, "Detailed entries with stats and ability"),
    "compact": ("Compact", 12, "One line per object"),
    "table": ("Table", 15, "Neat aligned columns"),
    "spotlight": ("Spotlight", 6, "First object big, the rest compact"),
    "minimal": ("Minimal", 20, "Just the names and numbers"),
    "bars": ("Bars", 8, "Big rarity bars under each name"),
    "grid": ("Grid", 12, "Small tiles in three columns"),
    "tree": ("Tree", 6, "Branch-style entries"),
    "dots": ("Dots", 14, "Receipt style with dotted lines"),
}

# most-caught leaderboard: name -> (button label, how far back; None = all time)
PERIODS = {
    "day": ("Day", timedelta(days=1)),
    "week": ("Week", timedelta(days=7)),
    "month": ("Month", timedelta(days=30)),
    "year": ("Year", timedelta(days=365)),
    "all": ("All time", None),
}
TOP_CACHE_SECONDS = 60  # leaderboard results are reused for this long (protects the database)
FOOTNOTE = "-# Is that a verity?"  # small text at the bottom of the menu

# ---- bad word reaction: shows one of two images (50/50) and nothing else
SCREAM_LABEL = "son???"  # the only thing left on the menu: a search button with this text
EXTRA_BAD_WORDS: set[str] = set()  # add your own words here (lowercase)
BAD_WORDS = {
    # swearing
    "fuck", "fucks", "fucked", "fucking", "fucker", "motherfucker", "fck", "fuk",
    "shit", "shits", "shitty", "bullshit", "bitch", "bitches", "bastard", "asshole",
    "ass", "dumbass", "jackass", "dick", "dickhead", "cock", "pussy", "cunt", "twat",
    "prick", "wanker", "bollocks", "arse", "piss", "damn", "goddamn", "whore", "slut",
    "douche", "douchebag", "tits", "cum", "jizz", "blowjob", "handjob", "porn",
    # slurs
    "nigger", "nigga", "niggas", "niggers", "chink", "gook", "spic", "wetback", "kike",
    "beaner", "coon", "paki", "raghead", "towelhead", "faggot", "faggots", "fag",
    "dyke", "tranny", "shemale", "retard", "retarded", "retards", "spastic",
}

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
    card_url: str = ""

    @property
    def emoji_str(self) -> str:
        return str(self.emoji) if self.emoji else ""


_cache: tuple[float, list[Entry]] | None = None
_warned_card = False


def load_entries(bot: "BallsDexBot") -> list[Entry]:
    """Cached wrapper so spamming /rarity or autocomplete doesn't rebuild the list every time."""
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > CACHE_SECONDS:
        _cache = (now, _build_entries(bot))
    return _cache[1]


def _card_url(ball) -> str:
    card = str(getattr(ball, "collection_card", "") or "")
    if not card:
        return ""
    if card.startswith(("http://", "https://")):
        return card
    if not CARD_URL_BASE:
        return ""
    return f"{CARD_URL_BASE.rstrip('/')}/media/{transform_media(card)}"


def _build_entries(bot: "BallsDexBot") -> list[Entry]:
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
                card_url=_card_url(ball),
            )
        )
    entries.sort(key=lambda e: (e.rarity, e.name.lower()))
    global _warned_card
    if not _warned_card and not CARD_URL_BASE and any(getattr(b, "collection_card", "") for b in balls.values()):
        _warned_card = True
        log.warning("rarity: CARD_URL_BASE is empty, so the 'View the card here!' link is hidden")
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


_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "+": "t"})
_BAD = {w for w in BAD_WORDS | EXTRA_BAD_WORDS}
_BAD_INSIDE = {w for w in _BAD if len(w) >= 5} | {"fuck", "shit"}  # also caught when letters are spaced/glued


def is_bad(query: str, entries: list["Entry"]) -> bool:
    """True if the search contains swearing or a slur. An object's real name is never flagged."""
    q = query.lower().strip()
    if any(e.name.lower() == q for e in entries):
        return False
    tokens = re.findall(r"[a-z]+", q.translate(_LEET))
    if any(t in _BAD for t in tokens):
        return True
    squashed = "".join(tokens)
    return any(w in squashed for w in _BAD_INSIDE)


def _load_bad_images() -> list[tuple[str, bytes]]:
    """The two images: embedded in bad_images.py, or bad1.webp / bad2.webp next to this file."""
    found: list[tuple[str, bytes]] = []
    try:
        from .bad_images import IMAGES  # type: ignore

        found = [(name, base64.b64decode(data)) for name, data in IMAGES.items()]
    except Exception:
        for name in ("bad1.webp", "bad2.webp"):
            f = Path(__file__).parent / name
            if f.exists():
                found.append((name, f.read_bytes()))
    if not found:
        log.warning("rarity: no bad-word images found (bad_images.py missing), that feature is off")
    return found


BAD_IMAGES = _load_bad_images()


def fancy(text: str) -> str:
    """Bold fraktur letters for titles (Discord can't change fonts, but Unicode can)."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D56C + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D586 + ord(ch) - ord("a")))
        else:
            out.append(ch)
    return "".join(out)


_SMALL_CAPS = dict(zip(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
))


def sc(text: str) -> str:
    """Small caps for labels: easy to read, looks neat. Ratings: ʀᴀʀɪᴛʏ"""
    return "".join(_SMALL_CAPS.get(ch.lower(), ch) for ch in text)


def _shift(text: str, upper: int, lower: int, digit: int | None = None) -> str:
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(upper + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(lower + ord(ch) - ord("a")))
        elif digit is not None and "0" <= ch <= "9":
            out.append(chr(digit + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


def bold_sans(text: str) -> str:
    """Bold sans-serif: clean and very readable (used for object names in the list)."""
    return _shift(text, 0x1D5D4, 0x1D5EE, 0x1D7EC)


def script(text: str) -> str:
    """Bold script: the fancy one (used for the object card title)."""
    return _shift(text, 0x1D4D0, 0x1D4EA)


def sans(text: str) -> str:
    """Plain sans-serif (Coolvetica-ish labels)."""
    return _shift(text, 0x1D5A0, 0x1D5BA, 0x1D7E2)


def mono(text: str) -> str:
    """Monospace (VCR OSD Mono-ish)."""
    return _shift(text, 0x1D670, 0x1D68A, 0x1D7F6)


def _map(upper: int, lower: int, digit: int | None = None, special: dict | None = None):
    """Build a text converter from Unicode alphabet start points."""
    special = special or {}

    def convert(text: str) -> str:
        out = []
        for ch in text:
            if ch in special:
                out.append(special[ch])
            elif "A" <= ch <= "Z":
                out.append(chr(upper + ord(ch) - 65))
            elif "a" <= ch <= "z":
                out.append(chr(lower + ord(ch) - 97))
            elif digit is not None and "0" <= ch <= "9":
                out.append(chr(digit + ord(ch) - 48))
            else:
                out.append(ch)
        return "".join(out)

    return convert


serif_bold = _map(0x1D400, 0x1D41A, 0x1D7CE)
serif_italic = _map(0x1D434, 0x1D44E, None, {"h": "\u210e"})
serif_bold_italic = _map(0x1D468, 0x1D482)
sans_italic = _map(0x1D608, 0x1D622)
sans_bold_italic = _map(0x1D63C, 0x1D656)
double_struck = _map(
    0x1D538, 0x1D552, 0x1D7D8,
    {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"},
)
fullwidth = _map(0xFF21, 0xFF41, 0xFF10)
_squared = _map(0x1F130, 0x1F130)
_neg_squared = _map(0x1F170, 0x1F170)
_CIRCLED_DIGITS = "⓪①②③④⑤⑥⑦⑧⑨"


def circled(text: str) -> str:
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x24B6 + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(0x24D0 + ord(ch) - 97))
        elif "0" <= ch <= "9":
            out.append(_CIRCLED_DIGITS[int(ch)])
        else:
            out.append(ch)
    return "".join(out)


def squared(text: str) -> str:
    return _squared(text.upper())


def neg_squared(text: str) -> str:
    return _neg_squared(text.upper())


def wide(text: str) -> str:
    """Letter-spaced bold sans."""
    out = []
    for i, ch in enumerate(text):
        out.append(ch)
        if not ch.isspace() and i + 1 < len(text) and not text[i + 1].isspace():
            out.append("\u2009")
    return bold_sans("".join(out))


@dataclass(frozen=True)
class FontStyle:
    label_name: str  # shown in the font picker and on the Font button
    title: callable  # the big list title
    obj: callable  # object names in the list
    card: callable  # object card title
    label: callable  # small labels, buttons, headings


def _u(name: str, fn) -> FontStyle:
    """One style for everything."""
    return FontStyle(name, fn, fn, fn, fn)


# Discord can't load real font files, so these are Unicode lookalikes. 20 fonts + Off.
FONTS: dict[str, FontStyle] = {
    "default": FontStyle("Default", fancy, bold_sans, script, sc),
    "coolvetica": FontStyle("Coolvetica", bold_sans, bold_sans, bold_sans, sans),
    "vcr": _u("VCR OSD", mono),
    "fe": FontStyle(
        "FE-Font",
        lambda t: bold_sans(t.upper()),
        lambda t: bold_sans(t.upper()),
        lambda t: bold_sans(t.upper()),
        lambda t: sans(t.upper()),
    ),
    "serif": _u("Bold Serif", serif_bold),
    "italic": _u("Italic Serif", serif_italic),
    "bolditalic": _u("Bold Italic", serif_bold_italic),
    "sansitalic": _u("Sans Italic", sans_italic),
    "sansbolditalic": _u("Sans Bold Italic", sans_bold_italic),
    "script": _u("Script", script),
    "fraktur": _u("Fraktur", fancy),
    "double": _u("Double Struck", double_struck),
    "smallcaps": _u("Small Caps", sc),
    "wide": _u("Wide", wide),
    "fullwidth": _u("Fullwidth", fullwidth),
    "circled": _u("Bubble", circled),
    "squared": _u("Squared", squared),
    "negsquared": _u("Blocks", neg_squared),
    "clean": _u("Clean Sans", sans),
    "arcade": _u("Arcade", lambda t: mono(t.upper())),
    "off": _u("Off", str),
}


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def rarity_fraction(r: float) -> float:
    """0.0 = rarest value on the bot ... 1.0 = most common (log scale, so small values stay readable)."""
    lo = max(RARITY_MIN, 1e-9)
    if r <= lo:
        return 0.0
    if r >= RARITY_MAX:
        return 1.0
    return math.log(r / lo) / math.log(RARITY_MAX / lo)


def rarity_bar(r: float) -> str:
    filled = max(1, round(rarity_fraction(r) * 10))
    return "▰" * filled + "▱" * (10 - filled)


def stat_bar(value: int, top: int, width: int = 8) -> str:
    filled = max(0, min(width, round(value / top * width))) if top else 0
    return "▰" * filled + "▱" * (width - filled)


def gradient_color(t: float) -> discord.Color:
    """Smooth multi-stop fade. t = 0.0 (rank 1, rarest) ... 1.0 (last rank)."""
    t = min(max(t, 0.0), 1.0)
    seg = t * (len(GRADIENT) - 1)
    i = int(seg)
    if i >= len(GRADIENT) - 1:
        return discord.Color.from_rgb(*GRADIENT[-1])
    f = seg - i
    a, b = GRADIENT[i], GRADIENT[i + 1]
    return discord.Color.from_rgb(*(round(x + (y - x) * f) for x, y in zip(a, b)))


def page_bar(page: int, total: int, width: int = 8) -> str:
    """A little slider showing where you are in the book: ▱▱▱◆▱▱▱▱"""
    if total <= 1:
        return "◆" + "▱" * (width - 1)
    pos = round(page / (total - 1) * (width - 1))
    return "▱" * pos + "◆" + "▱" * (width - 1 - pos)


def rarity_color(r: float) -> discord.Color:
    """Gold for the rarest objects, fading to grey for the most common."""
    f = rarity_fraction(r)
    rare, common = (255, 193, 7), (149, 165, 166)
    return discord.Color.from_rgb(*(round(a + (b - a) * f) for a, b in zip(rare, common)))


class ClaimStore:
    """Remembers which users already used the secret button (persisted to a JSON file)."""

    def __init__(self, path: Path = CLAIMS_FILE):
        self.path = path
        self.lock = asyncio.Lock()
        self.claimed: set[int] = set()
        for candidate in (path, Path.cwd() / path.name):
            try:
                self.claimed = {int(i) for i in json.loads(candidate.read_text(encoding="utf-8"))}
                self.path = candidate
                break
            except (FileNotFoundError, ValueError, OSError):
                continue

    def has(self, user_id: int) -> bool:
        return user_id in self.claimed

    def _save(self):
        """Never raises: if the folder is read-only, fall back to the working directory."""
        data = json.dumps(sorted(self.claimed))
        for candidate in (self.path, Path.cwd() / self.path.name):
            try:
                candidate.write_text(data, encoding="utf-8")
                self.path = candidate
                return
            except OSError:
                continue
        log.warning("rarity: could not save %s, claims only last until the bot restarts", self.path.name)

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


class PrefStore:
    """Per-user menu choices (font, hide 0.0), saved to a JSON file."""

    def __init__(self, path: Path = PREFS_FILE):
        self.path = path
        self.data: dict[str, dict] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.data = {str(k): dict(v) for k, v in raw.items()}
        except (FileNotFoundError, ValueError, TypeError, AttributeError):
            pass

    def get(self, user_id: int, key: str, default):
        return self.data.get(str(user_id), {}).get(key, default)

    def set(self, user_id: int, key: str, value):
        self.data.setdefault(str(user_id), {})[key] = value
        try:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except Exception:
            log.exception("could not save rarity prefs")


_top_cache: dict[str, tuple[float, list[tuple[int, int]]]] = {}


async def top_catches(period: str, limit: int = 25) -> list[tuple[int, int]]:
    """[(ball id, times caught)] for the period, most caught first."""
    cached = _top_cache.get(period)
    now = time.monotonic()
    if cached and now - cached[0] < TOP_CACHE_SECONDS:
        return cached[1]
    delta = PERIODS[period][1]
    since = datetime.now(timezone.utc) - delta if delta else None
    if DJANGO:
        from django.db.models import Count

        qs = BallInstance.objects.all()
        if since:
            qs = qs.filter(catch_date__gte=since)
        qs = qs.values("ball_id").annotate(total=Count("id")).order_by("-total")[:limit]
        rows = [(r["ball_id"], r["total"]) async for r in qs]
    else:
        from tortoise.functions import Count

        qs = BallInstance.all()
        if since:
            qs = qs.filter(catch_date__gte=since)
        raw = await (
            qs.annotate(total=Count("id")).group_by("ball_id").order_by("-total").limit(limit).values("ball_id", "total")
        )
        rows = [(r["ball_id"], r["total"]) for r in raw]
    _top_cache[period] = (now, rows)
    return rows


def pick_ball():
    pool = [b for b in balls.values() if getattr(b, "enabled", True)]
    if SECRET_BALL_ID is not None:
        fixed = next((b for b in pool if b.pk == SECRET_BALL_ID), None)
        if fixed:
            return fixed
    # BallsDex convention: higher rarity number = more common, so use it as the weight
    weights = [max(float(b.rarity), 0.0001) for b in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def pick_rarest():
    """The rarest enabled object (lowest rarity number). Ties are picked at random."""
    pool = [b for b in balls.values() if getattr(b, "enabled", True)]
    if SECRET_PHRASE_SKIP_ZERO:
        pool = [b for b in pool if float(b.rarity) > 0] or pool
    lowest = min(float(b.rarity) for b in pool)
    return random.choice([b for b in pool if float(b.rarity) == lowest])


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
        text = self.query.value.strip()
        if SECRET_PHRASE and "".join(text.lower().split()) == "".join(SECRET_PHRASE.lower().split()):
            if await self.paginator.redeem_phrase(interaction):
                return
        self.paginator.apply_search(text)
        await self.paginator.update(interaction)


class JumpModal(discord.ui.Modal, title="Jump to page"):
    number = discord.ui.TextInput(
        label="Page number",
        placeholder="e.g. 12",
        required=True,
        max_length=6,
    )

    def __init__(self, view: "RarityView"):
        super().__init__()
        self.paginator = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.number.value.strip())
        except ValueError:
            await interaction.response.defer()
            return
        self.paginator.page = max(0, min(n - 1, self.paginator.total_pages - 1))
        await self.paginator.update(interaction)


# ------------------------------------------------------------------- view


class RarityView(discord.ui.View):
    def __init__(
        self,
        bot: "BallsDexBot",
        author: discord.abc.User,
        entries: list[Entry],
        claims: ClaimStore,
        phrase_claims: ClaimStore,
        prefs: PrefStore,
    ):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.author = author
        self.claims = claims
        self.phrase_claims = phrase_claims
        self.prefs = prefs
        self.full = entries
        self.by_id = {e.id: e for e in entries}
        self.font = prefs.get(author.id, "font", FONT_DEFAULT)
        if self.font not in FONTS:
            self.font = FONT_DEFAULT
        self._max_atk = max((e.attack or 0 for e in entries), default=1) or 1
        self._max_hp = max((e.health or 0 for e in entries), default=1) or 1
        self.layout = prefs.get(author.id, "layout", LAYOUT_DEFAULT)
        if self.layout not in LAYOUTS:
            self.layout = LAYOUT_DEFAULT
        self.scream: tuple[str, bytes] | None = None  # set => only an image + the son??? button
        self._has_attachment = False
        self.top_period: str | None = None  # set => showing the most-caught leaderboard
        self.top_rows: list[tuple[Entry, int]] | None = []
        self.picking: str | bool = False  # "font" / "layout" while a picker is open
        self.hide_zero = bool(prefs.get(author.id, "hide_zero", HIDE_ZERO_DEFAULT))
        self.has_zero = any(e.rarity <= 0 for e in entries)
        self.all: list[Entry] = []
        self.rank: dict[int, int] = {}
        self._rebuild_pool()

        self.base = self.all
        self.sort_mode = 0
        self.entries = self.all
        self.results_title: str | None = None  # set => showing search results
        self.results_query = ""
        self.suggestions: list[Entry] = []
        self.page = 0
        self.focus: Entry | None = None  # set => showing the object card

        self.tip_index = random.randrange(len(TIPS))
        self.tips_playing = TIPS_AUTOPLAY
        self.message: discord.Message | None = None
        self._last_action = 0.0
        self._busy = False  # a click is being handled right now: ignore the rest
        self._last_defer = 0.0
        self._stranger_at = 0.0
        self._ticker: asyncio.Task | None = None
        self.refresh()

    # ----- state helpers
    @property
    def st(self) -> FontStyle:
        return FONTS[self.font]

    def _rebuild_pool(self):
        """self.all = what the menu shows (objects with rarity <= 0 hidden if the user chose so)."""
        shown = [e for e in self.full if not (self.hide_zero and e.rarity <= 0)]
        self.all = shown or self.full
        self.rank = {e.id: i + 1 for i, e in enumerate(self.all)}

    def _dot(self, e: Entry) -> str:
        return SPECTRUM[round(rarity_fraction(e.rarity) * (len(SPECTRUM) - 1))]

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.entries) / self.per_page))

    @property
    def per_page(self) -> int:
        return LAYOUTS[self.layout][1]

    def attachment(self) -> discord.File | None:
        if not self.scream:
            return None
        return discord.File(io.BytesIO(self.scream[1]), filename=self.scream[0])

    def page_entries(self) -> list[Entry]:
        start = self.page * self.per_page
        return self.entries[start : start + self.per_page]

    def set_results(
        self, title: str | None, query: str, matches: list[Entry], suggestions: list[Entry]
    ):
        self.focus = None
        self.page = 0
        self.results_title = title
        self.results_query = query
        self.base = matches
        self.sort_mode = 0
        self.entries = matches
        self.suggestions = suggestions

    def apply_search(self, query: str):
        self.scream = None
        if query and BAD_IMAGES and is_bad(query, self.full):
            self.scream = random.choice(BAD_IMAGES)  # 50% each
            self.focus = None
            self.picking = False
            self.easter = None
            return
        self.easter = EASTER.get(query.strip().lower())
        if not query:
            self.set_results(None, "", self.all, [])
            return
        matches, suggestions = search_entries(self.all, query)
        self.set_results(f"Results for “{short(query, 60)}”", query, matches, suggestions)

    def _detail_description(self, e: Entry) -> str:
        lines = [DIVIDER, f"{self.st.label('rarest')} {self._strip_for(e)} {self.st.label('common')}"]
        pool = self.entries or self.all
        ids = [x.id for x in pool]
        if e.id in ids and len(pool) > 1:
            i = ids.index(e.id)
            prev, nxt = pool[(i - 1) % len(pool)], pool[(i + 1) % len(pool)]
            lines.append(f"-# ◀ {self.st.obj(prev.name)}  •  {self.st.obj(nxt.name)} ▶")
        if e.card_url:
            lines.append(f"[View the card here!]({e.card_url})")
        lines.append(f"-# {self.st.label('Rarity scale')}: {RARITY_MIN:g} {self.st.label('(rarest)')} → {RARITY_MAX:g} {self.st.label('(most common)')}")
        return "\n".join(lines)

    def _list_color(self):
        """Gold on the first pages (rarest), fading to grey as you page towards the common ones."""
        shown = self.page_entries()
        return gradient_color(self._rank_t(shown[0])) if shown else EMBED_COLOR

    def _rank_t(self, e: Entry) -> float:
        """Where this object sits in the whole dex: 0.0 = rank 1 ... 1.0 = last."""
        return (self.rank[e.id] - 1) / max(len(self.all) - 1, 1)

    def _strip_for(self, e: Entry) -> str:
        """The colour strip with a marker at this object's place in the dex."""
        idx = round(self._rank_t(e) * (len(SPECTRUM) - 1))
        return "".join("🔘" if i == idx else sq for i, sq in enumerate(SPECTRUM))

    def _spectrum(self) -> str:
        """Strip for the current page, with end labels."""
        shown = self.page_entries()
        if not shown:
            return ""
        lab = self.st.label
        return f"{lab('rarest')} {self._strip_for(shown[0])} {lab('common')}"

    def _author(self, embed: discord.Embed):
        user = self.bot.user
        if user:
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

    def _step_focus(self, delta: int):
        pool = self.entries or self.all
        ids = [e.id for e in pool]
        try:
            i = ids.index(self.focus.id) if self.focus else 0
        except ValueError:
            i = 0
        self.focus = self.by_id[ids[(i + delta) % len(ids)]]

    async def redeem_phrase(self, interaction: discord.Interaction) -> bool:
        """The hidden search phrase. Everything is sent privately (only the person who typed it sees it)."""
        user_id = interaction.user.id
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if self.phrase_claims.has(user_id) or not await self.phrase_claims.try_claim(user_id):
                await interaction.followup.send(
                    "Shhh. You already used the secret phrase. It only works once.", ephemeral=True
                )
                return True
        except Exception:
            log.exception("Secret phrase claim failed")
            await interaction.followup.send(
                "Hm. Error occured, DM kaboomtsarbobm pwease.", ephemeral=True
            )
            return True

        try:
            ball = pick_rarest()
            instance = await give_ball(user_id, interaction.guild_id, ball)
        except Exception:
            log.exception("Secret phrase failed")
            await self.phrase_claims.release(user_id)
            await interaction.followup.send(
                "Hm. Error occured, DM kaboomtsarbobm pwease.",
                ephemeral=True,
            )
            return True

        entry = self.by_id.get(ball.pk)
        atk = getattr(instance, "attack_bonus", 0)
        hp = getattr(instance, "health_bonus", 0)
        prize = discord.Embed(
            title="Secret phrase unlocked",
            description=(
                f"{entry.emoji_str if entry else ''} **{ball.country}** is now yours.\n"
                f"Rarity `{ball.rarity:g}`\n"
                f"`ATK {atk:+d}%` • `HP {hp:+d}%`\n\n"
                "-# Shhh. This only works once."
            ),
            color=rarity_color(float(ball.rarity)),
        )
        if entry and entry.emoji:
            prize.set_thumbnail(url=entry.emoji.url)
        await interaction.followup.send(embed=prize, ephemeral=True)
        return True

    def apply_sort(self):
        if self.sort_mode == 1:
            self.entries = sorted(self.base, key=lambda e: (-e.rarity, e.name.lower()))
        elif self.sort_mode == 2:
            self.entries = sorted(self.base, key=lambda e: e.name.lower())
        else:
            self.entries = self.base

    def _edit_kwargs(self) -> dict:
        """Attach the image in image mode, and remove it again when leaving image mode."""
        file = self.attachment()
        if file:
            self._has_attachment = True
            return {"attachments": [file]}
        if self._has_attachment:
            self._has_attachment = False
            return {"attachments": []}
        return {}

    async def update(self, interaction: discord.Interaction):
        self.refresh()
        kwargs = self._edit_kwargs()
        try:
            await interaction.response.edit_message(embed=self.embed(), view=self, **kwargs)
        except discord.InteractionResponded:
            if self.scream:
                kwargs = {"attachments": [self.attachment()]}
            await interaction.edit_original_response(embed=self.embed(), view=self, **kwargs)
        except discord.HTTPException:
            log.warning("Rarity menu update failed (expired or rate limited)", exc_info=True)
        self._last_action = time.monotonic()

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
        failures = 0
        try:
            while not self.is_finished():
                await asyncio.sleep(TIP_INTERVAL)
                if self.is_finished() or not self.message:
                    break
                if not self.tips_playing:
                    continue
                # don't fight with a user who is clicking right now
                if self.scream or self._busy or time.monotonic() - self._last_action < 3:
                    continue
                self.tip_index = (self.tip_index + 1) % len(TIPS)
                try:
                    # embed only: re-sending all the components every tick is needless load
                    await self.message.edit(embed=self.embed())
                    failures = 0
                except discord.NotFound:
                    break  # message was deleted
                except Exception:
                    failures += 1
                    if failures >= 3:
                        log.warning("Rarity tip ticker stopped after repeated errors", exc_info=True)
                        break
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Rarity tip ticker crashed")

    def footer(self, prefix: str = "") -> str:
        return f"{prefix} {self.tip}"

    # ----- embeds
    def embed(self) -> discord.Embed:
        if self.scream:
            emb = discord.Embed(color=0x000000)
            emb.set_image(url=f"attachment://{self.scream[0]}")
            return emb
        emb = self._base_embed()
        if self.picking == "layout":
            hint = f"🤔 {self.st.label('Layout')}: {LAYOUTS[self.layout][0]} • {self.st.label('pick one below, or use the arrows')}"
            emb.description = f"-# {hint}\n{emb.description or ''}"
        elif self.picking:
            hint = f"🔤 {self.st.label('Font')}: {self.st.label_name} • {self.st.label('pick one below, or use the arrows')}"
            emb.description = f"-# {hint}\n{emb.description or ''}"
        emb.add_field(name="\u200b", value=FOOTNOTE, inline=False)
        return emb

    def _base_embed(self) -> discord.Embed:
        if self.top_period:
            return self.top_embed()
        if self.focus:
            return self.detail_embed(self.focus)
        if self.results_title and not self.entries:
            return self.no_results_embed()
        return self.list_embed()

    def entry_line(self, e: Entry) -> str:
        text = (
            f"> {self._dot(e)} **#{self.rank[e.id]}** · {self.st.obj(e.name)}\n"
            f"> {e.emoji_str} {self.st.label('Rarity')}: `{e.rarity:g}`  `{rarity_bar(e.rarity)}`"
        ).replace(">  ", "> ")
        if e.attack is not None and e.health is not None:
            text += f"\n> ⚔️ `{e.attack}`  ❤️ `{e.health}`"
        if e.ability:
            text += f"\n> *✦ {short(e.ability, 60)}*"
        return text

    def _compact_line(self, e: Entry) -> str:
        return f"{self._dot(e)} **#{self.rank[e.id]}** {e.emoji_str} {self.st.obj(e.name)} · `{e.rarity:g}`".replace("  ", " ")

    def render_body(self) -> str:
        shown = self.page_entries()
        if self.layout == "compact":
            return "\n".join(self._compact_line(e) for e in shown)
        if self.layout == "table":
            rows = [f"{'#':>4}  {'Name':<22} {'Rarity':>7}", "─" * 36]
            rows += [
                f"{self.rank[e.id]:>4}  {short(e.name.replace('`', chr(39)), 22):<22} {e.rarity:>7g}"
                for e in shown
            ]
            return "```\n" + "\n".join(rows) + "\n```"
        if self.layout == "minimal":
            return "\n".join(
                f"{self._dot(e)} `{self.rank[e.id]:>3}` {self.st.obj(e.name)} · **{e.rarity:g}**" for e in shown
            )
        if self.layout == "bars":
            blocks = []
            for e in shown:
                filled = max(1, round(rarity_fraction(e.rarity) * 20))
                blocks.append(
                    f"{self._dot(e)} **#{self.rank[e.id]}** · {self.st.obj(e.name)}  `{e.rarity:g}`\n"
                    f"`{'█' * filled}{'░' * (20 - filled)}`"
                )
            return "\n".join(blocks)
        if self.layout == "tree":
            blocks = []
            for e in shown:
                parts = [f"{self._dot(e)} **#{self.rank[e.id]}** · {self.st.obj(e.name)}"]
                parts.append(f"{e.emoji_str} {self.st.label('Rarity')} `{e.rarity:g}` `{rarity_bar(e.rarity)}`".replace("  ", " ").strip())
                if e.attack is not None and e.health is not None:
                    parts.append(f"⚔️ `{e.attack}`  ❤️ `{e.health}`")
                if e.ability:
                    parts.append(f"*✦ {short(e.ability, 60)}*")
                lines = ["╭ " + parts[0]]
                for i, part in enumerate(parts[1:], 1):
                    lines.append(("╰ " if i == len(parts) - 1 else "├ ") + part)
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)
        if self.layout == "dots":
            width = 34
            rows = []
            for e in shown:
                left = f"{self.rank[e.id]:>3} {short(e.name.replace('`', chr(39)), 22)}"
                right = f"{e.rarity:g}"
                rows.append(left + " " + "." * max(2, width - len(left) - len(right) - 2) + " " + right)
            return "```\n" + "\n".join(rows) + "\n```"
        if self.layout == "grid":
            return ""  # drawn as embed fields in list_embed
        if self.layout == "spotlight" and shown:
            top, rest = shown[0], shown[1:]
            lines = [
                f"### {self._dot(top)} #{self.rank[top.id]} · {self.st.card(top.name)}",
                f"{top.emoji_str} {self.st.label('Rarity')}: `{top.rarity:g}`  `{rarity_bar(top.rarity)}`".replace("  `", " `").strip(),
            ]
            if top.attack is not None and top.health is not None:
                lines.append(f"⚔️ `{top.attack}`  ❤️ `{top.health}`")
            if top.ability:
                lines.append(f"*✦ {short(top.ability, 80)}*")
            if top.description:
                lines.append(f"> {short(top.description, 180)}")
            text = "\n".join(lines)
            if rest:
                text += f"\n{DIVIDER}\n" + "\n".join(self._compact_line(e) for e in rest)
            return text
        return "\n\n".join(self.entry_line(e) for e in shown)

    def list_embed(self) -> discord.Embed:
        body = self.render_body()
        page_text = f"{self.st.label('Page')} {self.page + 1}/{self.total_pages}  {page_bar(self.page, self.total_pages)}  •  "
        start = self.page * self.per_page
        end = min(start + self.per_page, len(self.entries))
        showing = f"\n-# ❖ {self.st.label('Showing')} {start + 1}–{end} {self.st.label('of')} {len(self.entries)}" if self.entries else ""

        if self.results_title:
            count = len(self.entries)
            embed = discord.Embed(
                title=f"🔎 {self.results_title}",
                description=(
                    f"-# {self.st.label('Found')} **{count}** {self.st.label('object' + ('s' if count != 1 else ''))} • "
                    f"{self.st.label('pick one below to see its ability')}{showing}\n{DIVIDER}\n\n{body}"
                ),
                color=SEARCH_COLOR,
            )
        else:
            embed = discord.Embed(
                title=f"✦ {self.st.title(TITLE)} ✦",
                description=f"-# {self.st.label('Lower number = rarer')} • {len(self.all)} {self.st.label('objects')}{showing}\n{self._spectrum()}\n{DIVIDER}\n\n{body}",
                color=self._list_color(),
            )
        if getattr(self, "easter", None):
            embed.description = f"{embed.description}\n\n**{self.easter}**"
        if self.layout == "grid":
            for e in self.page_entries():
                embed.add_field(
                    name=short(f"{e.emoji_str} {self.st.obj(e.name)}".strip(), 250),
                    value=f"{self._dot(e)} #{self.rank[e.id]} · `{e.rarity:g}`\n`{rarity_bar(e.rarity)}`",
                    inline=True,
                )
        embed.set_footer(text=self.footer(page_text))
        self._author(embed)
        shown = self.page_entries()
        if shown and shown[0].emoji:
            embed.set_thumbnail(url=shown[0].emoji.url)
        return embed

    def top_embed(self) -> discord.Embed:
        label = PERIODS[self.top_period][0]
        embed = discord.Embed(
            title=f"🏆 {self.st.label('Most caught')} · {self.st.label(label)}", color=0xF1C40F
        )
        rows = self.top_rows
        if rows is None:
            embed.description = "Couldn't load the catches right now. Try again in a moment."
        elif not rows:
            embed.description = f"{DIVIDER}\n\nNobody caught anything here yet."
        else:
            top, count = rows[0]
            lines = [
                f"### 👑 {self.st.card(top.name)}",
                f"{top.emoji_str} **{count}** {self.st.label('catches')}".strip(),
                DIVIDER,
            ]
            for i, (e, n) in enumerate(rows[1:], 2):
                lines.append(
                    f"{self._dot(e)} **#{i}** {e.emoji_str} {self.st.obj(e.name)} · `{n}` `{stat_bar(n, count)}`".replace("  ", " ")
                )
            embed.description = "\n".join(lines)
            if top.emoji:
                embed.set_thumbnail(url=top.emoji.url)
        embed.set_footer(text=self.footer())
        self._author(embed)
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
        if getattr(self, "easter", None):
            text += f"\n\n**{self.easter}**"
        embed = discord.Embed(title="🔎 No results, sorry man.", description=text, color=ERROR_COLOR)
        embed.set_footer(text=self.footer())
        return embed

    def detail_embed(self, e: Entry) -> discord.Embed:
        embed = discord.Embed(
            title=f"❖ {e.emoji_str} {self.st.card(e.name)} ❖".replace("  ", " "),
            description=self._detail_description(e),
            color=gradient_color(self._rank_t(e)),
        )
        embed.add_field(
            name=self.st.label("Rarity"), value=f"`{e.rarity:g}`\n`{rarity_bar(e.rarity)}`", inline=True
        )
        embed.add_field(
            name=self.st.label("Tier"), value=(
                f"`#{self.rank[e.id]} / {len(self.all)}`\n"
                f"-# {self.st.label('top')} {max(1, math.ceil(self.rank[e.id] / len(self.all) * 100))}% • "
                f"{self.st.label('rarer than')} {round((len(self.all) - self.rank[e.id]) / len(self.all) * 100)}%"
            ),
            inline=True
        )
        if e.attack is not None and e.health is not None:
            embed.add_field(
                name=self.st.label("Stats"), value=(
                    f"`ATK {e.attack:>4}` `{stat_bar(e.attack, self._max_atk)}`\n"
                    f"`HP  {e.health:>4}` `{stat_bar(e.health, self._max_hp)}`"
                ), inline=True
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
        self._author(embed)
        return embed

    # ----- components
    def refresh(self):
        """Rebuild the buttons/selects for the current state."""
        self.clear_items()
        if self.scream:
            button = discord.ui.Button(label=SCREAM_LABEL, style=discord.ButtonStyle.secondary, row=0)
            button.callback = self._guard(self._search)
            self.add_item(button)
            return
        if self.picking:
            (self._add_layout_picker if self.picking == "layout" else self._add_font_picker)()
            return
        if self.top_period:
            for key, (name, _) in PERIODS.items():
                self._add_button(
                    name,
                    self._period_cb(key),
                    discord.ButtonStyle.success if key == self.top_period else discord.ButtonStyle.secondary,
                    row=0,
                )
            self._add_button("Back to list", self._top_close, discord.ButtonStyle.primary, "↩️", row=1)
            self._add_button("Quit", self._quit, discord.ButtonStyle.danger, "💔", row=1)
            return
        if self.focus:
            self._add_button(None, self._prev_obj, discord.ButtonStyle.secondary, "◀️")
            self._add_button("Back to list", self._back, discord.ButtonStyle.primary, "↩️")
            self._add_button(None, self._next_obj, discord.ButtonStyle.secondary, "▶️")
            self._add_button("Search", self._search, discord.ButtonStyle.secondary, "🔎")
            self._add_button("Quit", self._quit, discord.ButtonStyle.danger, "💔")
            self._add_settings(1)
            return

        # Row 0: inspect dropdown (doubles as the suggestions picker)
        options_src = self.page_entries() if self.entries else self.suggestions[:25]
        if options_src:
            select = discord.ui.Select(
                placeholder=self.st.label(
                    f"Inspect {self.page * self.per_page + 1}–{min((self.page + 1) * self.per_page, len(self.entries))}"
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
            select.callback = self._guard(self._select)
            self.add_item(select)

        # Row 1: search / clear
        self._add_button("Search", self._search, discord.ButtonStyle.secondary, "🔎", row=1)
        if self.entries:
            self._add_button(
                SORT_LABELS[self.sort_mode], self._sort, discord.ButtonStyle.secondary, "↕️", row=1
            )
        if self.total_pages > 1:
            self._add_button("Jump to page", self._jump, discord.ButtonStyle.secondary, "🔢", row=1)
        if self.entries:
            self._add_button("Random", self._random, discord.ButtonStyle.secondary, "🎲", row=1)
        if self.results_title:
            self._add_button("Clear", self._clear, discord.ButtonStyle.secondary, "✖️", row=1)

        # Row 2: navigation
        no_prev = self.page <= 0
        no_next = self.page >= self.total_pages - 1
        self._add_button("≪", self._first, discord.ButtonStyle.secondary, row=2, disabled=no_prev)
        self._add_button("Back", self._prev, discord.ButtonStyle.primary, "⬅️", row=2, disabled=no_prev)
        self._add_button("Next", self._next, discord.ButtonStyle.primary, "➡️", row=2, disabled=no_next)
        self._add_button("≫", self._last, discord.ButtonStyle.secondary, row=2, disabled=no_next)
        self._add_button("Quit", self._quit, discord.ButtonStyle.danger, "💔", row=2)

        # Row 3: tips toggle
        self._add_settings(3)

        # Row 4: the secret button (disappears once this user has used it)
        if not self.claims.has(self.author.id):
            self._add_button("·", self._secret, discord.ButtonStyle.secondary, row=4)

    def _add_font_picker(self):
        select = discord.ui.Select(
            placeholder=self.st.label("Choose a font"),
            options=[
                discord.SelectOption(
                    label=f.label_name,
                    value=key,
                    description=short(f.obj("The Codex Archives"), 100),
                    default=(key == self.font),
                )
                for key, f in FONTS.items()
            ],
            row=0,
        )
        select.callback = self._guard(self._pick_font)
        self.add_item(select)
        self._add_button(None, self._font_prev, discord.ButtonStyle.secondary, "◀️", row=1)
        self._add_button("Done", self._font_done, discord.ButtonStyle.success, "✅", row=1)
        self._add_button(None, self._font_next, discord.ButtonStyle.secondary, "▶️", row=1)

    def _add_layout_picker(self):
        select = discord.ui.Select(
            placeholder=self.st.label("Choose a layout"),
            options=[
                discord.SelectOption(
                    label=name, value=key, description=blurb, default=(key == self.layout)
                )
                for key, (name, _, blurb) in LAYOUTS.items()
            ],
            row=0,
        )
        select.callback = self._guard(self._pick_layout)
        self.add_item(select)
        self._add_button(None, self._layout_prev, discord.ButtonStyle.secondary, "◀️", row=1)
        self._add_button("Done", self._font_done, discord.ButtonStyle.success, "✅", row=1)
        self._add_button(None, self._layout_next, discord.ButtonStyle.secondary, "▶️", row=1)

    def _add_settings(self, row: int):
        self._add_tips_button(row)
        self._add_button(
            f"Font: {self.st.label_name}", self._font, discord.ButtonStyle.secondary, "🔤", row=row
        )
        if not self.focus:
            self._add_button(
                f"Layout: {LAYOUTS[self.layout][0]}", self._layout, discord.ButtonStyle.secondary, "🤔", row=row
            )
            self._add_button("Top catches", self._top_open, discord.ButtonStyle.secondary, "🏆", row=row)
        if self.has_zero:
            on = self.hide_zero
            self._add_button(
                "Hide 0.0: On" if on else "Hide 0.0: Off",
                self._hide_zero,
                discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                "😂",
                row=row,
            )

    def _add_tips_button(self, row: int):
        on = self.tips_playing
        self._add_button(
            "Tips: On" if on else "Tips: Off",
            self._tips,
            discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
            "💡",
            row=row,
        )

    def _add_button(self, label, callback, style, emoji=None, row=None, disabled=False):
        button = discord.ui.Button(
            label=self.st.label(label) if label else label, style=style, emoji=emoji, row=row, disabled=disabled
        )
        button.callback = self._guard(callback)
        self.add_item(button)

    def _guard(self, callback):
        """Run a click handler so errors never escape and clicks can't overlap."""

        async def wrapper(interaction: discord.Interaction):
            try:
                await callback(interaction)
            except discord.InteractionResponded:
                pass
            except discord.HTTPException:
                log.warning("Rarity menu click failed (expired or rate limited)", exc_info=True)
            except Exception:
                log.exception("Rarity menu click crashed")
            finally:
                self._busy = False
                self._last_action = time.monotonic()

        return wrapper

    # ----- callbacks
    async def _select(self, interaction: discord.Interaction):
        chosen = int(interaction.data["values"][0])  # type: ignore
        self.focus = self.by_id[chosen]
        await self.update(interaction)

    async def _back(self, interaction: discord.Interaction):
        self.focus = None
        await self.update(interaction)

    async def _search(self, interaction: discord.Interaction):
        modal = SearchModal(self)
        if self.scream:
            modal.title = SCREAM_LABEL
            modal.query.label = SCREAM_LABEL
            modal.query.placeholder = SCREAM_LABEL
        await interaction.response.send_modal(modal)

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

    async def _sort(self, interaction: discord.Interaction):
        self.sort_mode = (self.sort_mode + 1) % len(SORT_LABELS)
        self.apply_sort()
        self.page = 0
        await self.update(interaction)

    async def _tips(self, interaction: discord.Interaction):
        self.tips_playing = not self.tips_playing
        if self.tips_playing:
            self.tip_index = (self.tip_index + 1) % len(TIPS)  # show the next one right away
        await self.update(interaction)

    async def _font(self, interaction: discord.Interaction):
        self.picking = "font"
        await self.update(interaction)

    def _set_font(self, key: str):
        if key in FONTS:
            self.font = key
            self.prefs.set(self.author.id, "font", key)

    async def _pick_font(self, interaction: discord.Interaction):
        self._set_font(interaction.data["values"][0])  # type: ignore
        await self.update(interaction)

    async def _font_step(self, interaction: discord.Interaction, delta: int):
        keys = list(FONTS)
        self._set_font(keys[(keys.index(self.font) + delta) % len(keys)])
        await self.update(interaction)

    async def _font_prev(self, interaction: discord.Interaction):
        await self._font_step(interaction, -1)

    async def _font_next(self, interaction: discord.Interaction):
        await self._font_step(interaction, 1)

    async def _font_done(self, interaction: discord.Interaction):
        self.picking = False
        await self.update(interaction)

    async def _layout(self, interaction: discord.Interaction):
        self.picking = "layout"
        await self.update(interaction)

    def _set_layout(self, key: str):
        if key in LAYOUTS:
            first = self.page * self.per_page
            self.layout = key
            self.prefs.set(self.author.id, "layout", key)
            self.page = min(first // self.per_page, self.total_pages - 1)

    async def _pick_layout(self, interaction: discord.Interaction):
        self._set_layout(interaction.data["values"][0])  # type: ignore
        await self.update(interaction)

    async def _layout_step(self, interaction: discord.Interaction, delta: int):
        keys = list(LAYOUTS)
        self._set_layout(keys[(keys.index(self.layout) + delta) % len(keys)])
        await self.update(interaction)

    async def _layout_prev(self, interaction: discord.Interaction):
        await self._layout_step(interaction, -1)

    async def _layout_next(self, interaction: discord.Interaction):
        await self._layout_step(interaction, 1)

    # ----- most caught
    async def _load_top(self, period: str):
        self.top_period = period
        try:
            rows = []
            for ball_id, total in await top_catches(period):
                e = self.by_id.get(ball_id)
                if e is None or (self.hide_zero and e.rarity <= 0):
                    continue
                rows.append((e, total))
            self.top_rows = rows[:10]
        except Exception:
            log.exception("Most-caught lookup failed")
            self.top_rows = None

    def _period_cb(self, period: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()  # the database can take a moment
            await self._load_top(period)
            await self.update(interaction)

        return callback

    async def _top_open(self, interaction: discord.Interaction):
        await self._period_cb("day")(interaction)

    async def _top_close(self, interaction: discord.Interaction):
        self.top_period = None
        await self.update(interaction)

    async def _hide_zero(self, interaction: discord.Interaction):
        self.hide_zero = not self.hide_zero
        self.prefs.set(self.author.id, "hide_zero", self.hide_zero)
        self._rebuild_pool()
        self.focus = None
        self.apply_search(self.results_query)  # rebuild the current list/search from the new pool
        await self.update(interaction)

    async def _prev_obj(self, interaction: discord.Interaction):
        self._step_focus(-1)
        await self.update(interaction)

    async def _next_obj(self, interaction: discord.Interaction):
        self._step_focus(1)
        await self.update(interaction)

    async def _random(self, interaction: discord.Interaction):
        self.focus = random.choice(self.entries or self.all)
        await self.update(interaction)

    async def _jump(self, interaction: discord.Interaction):
        await interaction.response.send_modal(JumpModal(self))

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
        now = time.monotonic()
        if interaction.user.id != self.author.id:
            if now - self._stranger_at > 3:  # don't answer every click of a spammer
                self._stranger_at = now
                try:
                    await interaction.response.send_message("Who are YOU??", ephemeral=True)
                except discord.HTTPException:
                    pass
            return False
        # busy (a click is still being handled; 10s = safety net) or too fast: ignore
        if (self._busy and now - self._last_action < 10) or now - self._last_action < CLICK_COOLDOWN:
            if now - self._last_defer > 1.0:  # acknowledge now and then, not every spam click
                self._last_defer = now
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass
            return False
        self._busy = True
        self._last_action = now
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("Error in rarity menu", exc_info=error)

    async def close(self):
        """Shut this rarity list down (used when the same user opens a new one)."""
        self.stop()
        self.stop_ticker()
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        self.stop_ticker()
        for item in self.children:
            item.disabled = True  # type: ignore
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# -------------------------------------------------------------------- cog


class Rarity(commands.Cog):
    """Paginated rarity list with search, suggestions, ability cards and rotating tips."""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.claims = ClaimStore()
        self.phrase_claims = ClaimStore(PHRASE_CLAIMS_FILE)
        self.prefs = PrefStore()
        self.active: weakref.WeakValueDictionary[int, RarityView] = weakref.WeakValueDictionary()

    async def cog_unload(self):
        for view in list(self.active.values()):
            view.stop()
            view.stop_ticker()

    @app_commands.command(name="rarity", description="Show the rarity list of every object")
    @app_commands.describe(name="Jump straight to an object (suggestions appear as you type)")
    async def rarity(self, interaction: discord.Interaction, name: str | None = None):
        old = self.active.get(interaction.user.id)
        if old is not None:
            await old.close()

        entries = load_entries(self.bot)
        view = RarityView(
            self.bot, interaction.user, entries, self.claims, self.phrase_claims, self.prefs
        )

        if name:
            exact = next((e for e in view.all if e.name.lower() == name.lower()), None)
            if exact:
                view.focus = exact
            else:
                view.apply_search(name)
            view.refresh()

        first_file = view.attachment()
        if first_file:
            view._has_attachment = True
        await interaction.response.send_message(
            embed=view.embed(), view=view, **({"file": first_file} if first_file else {})
        )
        view.message = await interaction.original_response()
        view.start_ticker()
        self.active[interaction.user.id] = view

    @rarity.autocomplete("name")
    async def name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        entries = load_entries(self.bot)
        if self.prefs.get(interaction.user.id, "hide_zero", HIDE_ZERO_DEFAULT):
            entries = [e for e in entries if e.rarity > 0] or entries
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
