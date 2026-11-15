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
import tempfile
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

try:
    from bd_models.models import BallInstance, Player, balls

    DJANGO = True
except ImportError:
    from ballsdex.core.models import BallInstance, Player, balls

    DJANGO = False

try:
    from bd_models.models import transform_media
except ImportError:

    def transform_media(path: str) -> str:
        return path


log = logging.getLogger(__name__)


def _protect_view_store():
    try:
        from discord.ui.view import ViewStore
    except Exception:
        log.warning("rarity: could not protect discord.py's button handling", exc_info=True)
        return
    original = ViewStore.dispatch_view
    if getattr(original, "_rarity_safe", False):
        return

    def dispatch_view(self, component_type, custom_id, interaction):
        try:
            return original(self, component_type, custom_id, interaction)
        except AttributeError as exc:
            if "_dispatch_item" not in str(exc):
                raise
            log.debug("rarity: ignored a click on a button that was already replaced")

    dispatch_view._rarity_safe = True
    ViewStore.dispatch_view = dispatch_view


_protect_view_store()


def _pick_data_dir() -> Path:
    candidates = []
    if os.environ.get("RARITY_DATA_DIR"):
        candidates.append(Path(os.environ["RARITY_DATA_DIR"]))
    candidates += [
        Path("/code/config"),
        Path(__file__).parent,
        Path.cwd(),
        Path("/data"),
        Path(tempfile.gettempdir()) / "rarity",
    ]
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".rarity_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return folder
        except OSError:
            continue
    return Path(__file__).parent


DATA_DIR = _pick_data_dir()
log.info("rarity: saving its data in %s (set RARITY_DATA_DIR to choose another folder)", DATA_DIR)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

TITLE = "The Codex Archives"
TIMEOUT = 600
TIP_INTERVAL = 10
CLICK_COOLDOWN = 0.6
BUTTON_COOLDOWN = 1.5
SPAM_CLICKS = 10
SPAM_LOCK_SECONDS = 10
CACHE_SECONDS = 30

RARITY_MIN = 1.0
RARITY_MAX = 100.0
CARD_URL_BASE = os.environ.get("RARITY_CARD_URL_BASE", "")
TIPS_AUTOPLAY = True
DIVIDER = "✦━━━━━━━━━❖━━━━━━━━━✦"
GRADIENT = [(255, 193, 7), (255, 112, 67), (236, 64, 122), (126, 87, 194), (66, 133, 244), (149, 165, 166)]
SPECTRUM = ["🟨", "🟧", "🟧", "🟥", "🟥", "🟪", "🟪", "🟦", "🟦", "⬜"]
SORT_LABELS = ["Sort: Default", "Sort: Most common", "Sort: A–Z"]

EASTER = {
    "verity": "ask me anything",
    "nintendo": "nintendo is epic",
    "codex": "brother you know the rarity",
    "codexy": "brother you know the rarity",
    "spongebob": "IM SPONGEBOB",
}

DB_TIMEOUT = 10.0
DB_STATEMENT_MS = 4000

FONT_DEFAULT = "default"
HIDE_ZERO_DEFAULT = False
PREFS_FILE = DATA_DIR / "rarity_prefs.json"

LAYOUT_DEFAULT = "cards"
LAYOUTS = {
    "cards": ("Cards", 7, "Detailed entries with stats and ability"),
    "compact": ("Compact", 12, "One line per object"),
    "table": ("Table", 15, "Neat aligned columns"),
    "spotlight": ("Spotlight", 6, "First object big, the rest compact"),
    "bars": ("Bars", 8, "Big rarity bars under each name"),
    "grid": ("Grid", 12, "Small tiles in three columns"),
    "tree": ("Tree", 6, "Branch-style entries"),
    "dots": ("Dots", 14, "Receipt style with dotted lines"),
    "banner": ("Banner", 5, "Big headings with the rarity under each"),
    "ledger": ("Ledger", 10, "Boxed table with borders"),
    "stack": ("Stack", 6, "Each object with its own rarity strip"),
    "timeline": ("Timeline", 8, "A vertical line with a node per object"),
    "sheet": ("Sheet", 6, "Rarity, attack and health bars"),
    "quote": ("Quote", 6, "Each object in its own quote block"),
    "gauge": ("Gauge", 8, "A gauge and the top percent for each object"),
    "duo": ("Duo", 20, "Two columns of names in a box"),
    "rail": ("Rail", 8, "A coloured rail down the side of each object"),
    "statline": ("Statline", 10, "Attack, health and rarity on one line"),
    "plaque": ("Plaque", 6, "Framed names with dividers"),
    "heat": ("Heat", 10, "A row of heat squares for each object"),
}

PERIODS = {
    "day": ("Day", timedelta(days=1)),
    "week": ("Week", timedelta(days=7)),
    "month": ("Month", timedelta(days=30)),
    "year": ("Year", timedelta(days=365)),
    "all": ("All time", None),
}
TOP_CACHE_SECONDS = 60
FOOTNOTE = "-# Is that a verity?"

SCREAM_LABEL = "son???"
EXTRA_BAD_WORDS: set[str] = set()
BAD_WORDS = {
    "fuck", "fucks", "fucked", "fucking", "fucker", "motherfucker", "fck", "fuk",
    "shit", "shits", "shitty", "bullshit", "bitch", "bitches", "bastard", "asshole",
    "ass", "dumbass", "jackass", "dick", "dickhead", "cock", "pussy", "cunt", "twat",
    "prick", "wanker", "bollocks", "arse", "piss", "damn", "goddamn", "whore", "slut",
    "douche", "douchebag", "tits", "cum", "jizz", "blowjob", "handjob", "porn",
    "nigger", "nigga", "niggas", "niggers", "chink", "gook", "spic", "wetback", "kike",
    "beaner", "coon", "paki", "raghead", "towelhead", "faggot", "faggots", "fag",
    "dyke", "tranny", "shemale", "retard", "retarded", "retards", "spastic",
}

EMBED_COLOR = 0x5865F2
SEARCH_COLOR = 0x57F287
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
    named = [(e.name.lower(), e) for e in entries]
    starts = [e for n, e in named if n.startswith(q)]
    seen = {e.id for e in starts}
    contains = [e for n, e in named if q in n and e.id not in seen]
    matches = starts + contains
    suggestions: list[Entry] = []
    if not matches:
        by_name = dict(named)
        close = difflib.get_close_matches(q, list(by_name), n=10, cutoff=0.4)
        suggestions = [by_name[n] for n in close]
    return matches, suggestions


_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "+": "t"})
_BAD = {w for w in BAD_WORDS | EXTRA_BAD_WORDS}
_BAD_INSIDE = {w for w in _BAD if len(w) >= 5} | {"fuck", "shit"}


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
        from .bad_images import IMAGES

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


gothic = _map(0x1D504, 0x1D51E, None, {"C": "ℭ", "H": "ℌ", "I": "ℑ", "R": "ℜ", "Z": "ℨ"})
cursive = _map(
    0x1D49C, 0x1D4B6, None,
    {"B": "ℬ", "E": "ℰ", "F": "ℱ", "H": "ℋ", "I": "ℐ", "L": "ℒ", "M": "ℳ", "R": "ℛ", "e": "ℯ", "g": "ℊ", "o": "ℴ"},
)
_dark_bubble = _map(0x1F150, 0x1F150)


def dark_bubble(text: str) -> str:
    return _dark_bubble(text.upper())


def brackets(text: str) -> str:
    return re.sub(r"(\S+)", r"【\1】", text)


bold_script = _map(0x1D4D0, 0x1D4EA)


@dataclass(frozen=True)
class FontStyle:
    label_name: str
    title: callable
    obj: callable
    card: callable
    label: callable


def _u(name: str, fn) -> FontStyle:
    """One style for everything."""
    return FontStyle(name, fn, fn, fn, fn)


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
    "gothic": _u("Gothic", gothic),
    "cursive": _u("Cursive", cursive),
    "darkbubble": _u("Dark Bubble", dark_bubble),
    "brackets": _u("Brackets", brackets),
    "boldscript": _u("Bold Script", bold_script),
    "off": _u("Off", str),
}


@dataclass(frozen=True)
class Theme:
    divider: str
    frame: tuple[str, str]
    card_frame: tuple[str, str]
    bar: tuple[str, str]
    bullet: str
    mark: str


THEME = Theme(DIVIDER, ("✦", "✦"), ("❖", "❖"), ("▰", "▱"), "✦", "❖")


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
        if getattr(self, "_timer", None):
            return
        try:
            self._timer = asyncio.get_running_loop().call_later(2.0, self.flush)
        except RuntimeError:
            self.flush()

    def flush(self):
        self._timer = None
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            log.exception("could not save rarity prefs")


_top_cache: dict[str, tuple[float, list[tuple[int, int]]]] = {}


def _ttl(period: str) -> float:
    return TOP_CACHE_SECONDS if period == "day" else TOP_CACHE_SECONDS * 5


async def _run_limited(qs) -> list:
    from asgiref.sync import sync_to_async

    def run():
        from django.db import connection, transaction

        if connection.vendor == "postgresql":
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {int(DB_STATEMENT_MS)}")
                return list(qs)
        return list(qs)

    return await sync_to_async(run)()


_history_state = {"skip_until": 0.0}


async def _top_catches_raw(period: str, limit: int = 25) -> list[tuple[int, int]]:
    """[(ball id, times caught)] for the period, most caught first."""
    cached = _top_cache.get(period)
    now = time.monotonic()
    if cached and now - cached[0] < _ttl(period):
        return cached[1]
    delta = PERIODS[period][1]
    since = datetime.now(timezone.utc) - delta if delta else None
    if DJANGO:
        from django.db.models import Count

        qs = BallInstance.objects.all()
        if since:
            qs = qs.filter(catch_date__gte=since)
        qs = qs.values("ball_id").annotate(total=Count("id")).order_by("-total")[:limit]
        rows = [(r["ball_id"], r["total"]) for r in await _run_limited(qs)]
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


def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


_catcher_cache: dict[str, tuple[float, tuple[list[tuple[int, int]], str]]] = {}


async def _top_catchers_raw(period: str, limit: int = 10) -> tuple[list[tuple[int, int]], str]:
    """([(discord id, catches)], method) for the period, most catches first.

    method says how catches were attributed:
      "original" = a stored original-catcher field, "history" = first giver in the trade history
      (both keep counting balls that were traded away), "owner" = whoever holds the ball now.
    """
    cached = _catcher_cache.get(period)
    now = time.monotonic()
    if cached and now - cached[0] < _ttl(period):
        return cached[1]
    delta = PERIODS[period][1]
    since = datetime.now(timezone.utc) - delta if delta else None
    result: tuple[list[tuple[int, int]], str] | None = None

    if DJANGO:
        import bd_models.models as bdm
        from django.db.models import BigIntegerField, Count, F, OuterRef, Subquery
        from django.db.models.functions import Coalesce

        base = BallInstance.objects.all()
        if since:
            base = base.filter(catch_date__gte=since)

        async def collect(qs, key: str) -> list[tuple[int, int]]:
            return [(r[key], r["total"]) for r in await _run_limited(qs) if r[key] is not None]

        pairs, method = None, "owner"
        field = next(
            (f for f in ("original_player", "first_owner", "original_owner", "catcher") if _has_field(BallInstance, f)),
            None,
        )
        if field:
            try:
                qs = base.values(f"{field}_id").annotate(total=Count("id")).order_by("-total")[:limit]
                pairs, method = await asyncio.wait_for(collect(qs, f"{field}_id"), 6.0), "original"
            except Exception:
                log.warning("rarity: original-catcher query failed", exc_info=True)
        trade_obj, trade = getattr(bdm, "TradeObject", None), getattr(bdm, "Trade", None)
        if pairs is None and trade_obj and trade and time.monotonic() >= _history_state["skip_until"]:
            date_field = next((f for f in ("date", "trade_date", "created_at") if _has_field(trade, f)), None)
            if date_field and all(_has_field(trade_obj, f) for f in ("ballinstance", "player", "trade")):
                try:
                    first_giver = (
                        trade_obj.objects.filter(ballinstance=OuterRef("pk"))
                        .order_by(f"trade__{date_field}")
                        .values("player_id")[:1]
                    )
                    qs = (
                        base.annotate(
                            catcher=Coalesce(Subquery(first_giver), F("player_id"), output_field=BigIntegerField())
                        )
                        .values("catcher")
                        .annotate(total=Count("id"))
                        .order_by("-total")[:limit]
                    )
                    pairs, method = await asyncio.wait_for(collect(qs, "catcher"), 6.0), "history"
                except Exception:
                    _history_state["skip_until"] = time.monotonic() + 600
                    log.warning("rarity: trade-history catcher query failed, using current owners for 10 minutes", exc_info=True)
        if pairs is None:
            qs = base.values("player_id").annotate(total=Count("id")).order_by("-total")[:limit]
            pairs, method = await collect(qs, "player_id"), "owner"

        ids = [pk for pk, _ in pairs]
        discord_ids = {
            r["pk"]: r["discord_id"] async for r in Player.objects.filter(pk__in=ids).values("pk", "discord_id")
        }
        result = ([(discord_ids[pk], n) for pk, n in pairs if pk in discord_ids], method)
    else:
        from tortoise.functions import Count

        qs = BallInstance.all()
        if since:
            qs = qs.filter(catch_date__gte=since)
        raw = await qs.annotate(total=Count("id")).group_by("player_id").order_by("-total").limit(limit).values(
            "player_id", "total"
        )
        players = await Player.filter(pk__in=[r["player_id"] for r in raw]).values("pk", "discord_id")
        discord_ids = {p["pk"]: p["discord_id"] for p in players}
        result = ([(discord_ids[r["player_id"]], r["total"]) for r in raw if r["player_id"] in discord_ids], "owner")

    _catcher_cache[period] = (now, result)
    return result


_top_locks: dict[str, asyncio.Lock] = {}
_db_slots = asyncio.Semaphore(1)
_db_waiting = [0]
_db_fail_until: dict[str, float] = {}


async def _guarded_lookup(key: str, factory):
    if time.monotonic() < _db_fail_until.get(key, 0.0):
        raise asyncio.TimeoutError("leaderboard lookup failed a moment ago")
    if _db_waiting[0] >= 6:
        raise asyncio.TimeoutError("too many leaderboard lookups waiting")
    _db_waiting[0] += 1
    try:
        lock = _top_locks.setdefault(key, asyncio.Lock())
        async with lock:
            async with _db_slots:
                try:
                    return await asyncio.wait_for(factory(), DB_TIMEOUT)
                except Exception:
                    _db_fail_until[key] = time.monotonic() + 20
                    raise
    finally:
        _db_waiting[0] -= 1


async def top_catches(period: str, limit: int = 25) -> list[tuple[int, int]]:
    cached = _top_cache.get(period)
    if cached and time.monotonic() - cached[0] < _ttl(period):
        return cached[1]
    return await _guarded_lookup(f"balls:{period}", lambda: _top_catches_raw(period, limit))


async def top_catchers(period: str, limit: int = 10) -> tuple[list[tuple[int, int]], str]:
    cached = _catcher_cache.get(period)
    if cached and time.monotonic() - cached[0] < _ttl(period):
        return cached[1]
    return await _guarded_lookup(f"players:{period}", lambda: _top_catchers_raw(period, limit))


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


class RarityView(discord.ui.View):
    def __init__(
        self,
        bot: "BallsDexBot",
        author: discord.abc.User,
        entries: list[Entry],
        prefs: PrefStore,
    ):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.author = author
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
        self.scream: tuple[str, bytes] | None = None
        self._has_attachment = False
        self.top_period: str | None = None
        self.top_rows: list[tuple[Entry, int]] | None = []
        self.top_kind = "balls"
        self.top_players: list[tuple[int, int]] | None = []
        self.top_method = "owner"
        self.menu = False
        self.picking: str | bool = False
        self.hide_zero = bool(prefs.get(author.id, "hide_zero", HIDE_ZERO_DEFAULT))
        self.has_zero = any(e.rarity <= 0 for e in entries)
        self.all: list[Entry] = []
        self.rank: dict[int, int] = {}
        self._rebuild_pool()

        self.base = self.all
        self.sort_mode = 0
        self.entries = self.all
        self.results_title: str | None = None
        self.results_query = ""
        self.suggestions: list[Entry] = []
        self.page = 0
        self.focus: Entry | None = None

        self.tip_index = random.randrange(len(TIPS))
        self.tips_playing = TIPS_AUTOPLAY
        self.message: discord.Message | None = None
        self._last_action = 0.0
        self._busy = False
        self._last_defer = 0.0
        self._last_slow = 0.0
        self._button_at: dict[str, float] = {}
        self._ignored: list[float] = []
        self._locked_until = 0.0
        self._stranger_at = 0.0
        self._ticker: asyncio.Task | None = None
        self.refresh()

    @property
    def st(self) -> FontStyle:
        return FONTS[self.font]

    def _rebuild_pool(self):
        """self.all = what the menu shows (objects with rarity <= 0 hidden if the user chose so)."""
        shown = [e for e in self.full if not (self.hide_zero and e.rarity <= 0)]
        self.all = shown or self.full
        self.rank = {e.id: i + 1 for i, e in enumerate(self.all)}

    @property
    def th(self) -> Theme:
        return THEME

    def _pri(self):
        return discord.ButtonStyle.primary

    def bar(self, r: float) -> str:
        filled = max(1, round(rarity_fraction(r) * 10))
        full, empty = self.th.bar
        return full * filled + empty * (10 - filled)

    def sbar(self, value: int, top: int, width: int = 8) -> str:
        filled = max(0, min(width, round(value / top * width))) if top else 0
        full, empty = self.th.bar
        return full * filled + empty * (width - filled)

    def _frame(self, text: str, card: bool = False) -> str:
        left, right = self.th.card_frame if card else self.th.frame
        return f"{left} {text.strip()} {right}".strip()

    @property
    def daily(self) -> Entry | None:
        """Object of the day: the same for everyone, changes at midnight UTC."""
        today = datetime.now(timezone.utc).date().isoformat()
        cached = getattr(self, "_daily_cache", None)
        if cached and cached[0] == today:
            return cached[1]
        pool = sorted((e for e in self.full if e.rarity > 0), key=lambda e: e.id) or sorted(self.full, key=lambda e: e.id)
        pick = random.Random(today + "codex").choice(pool) if pool else None
        self._daily_cache = (today, pick)
        return pick

    def _star(self, e: Entry) -> str:
        daily = self.daily
        return " 🌟" if daily and daily.id == e.id else ""

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
            self.scream = random.choice(BAD_IMAGES)
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
        lines = [f"{self.st.label('rarest')} {self._strip_for(e)} {self.st.label('common')}"]
        if self._star(e):
            lines.insert(0, f"🌟 **{self.st.label('Object of the day')}**")
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
                if self.scream or self._busy or time.monotonic() - self._last_action < 3:
                    continue
                self.tip_index = (self.tip_index + 1) % len(TIPS)
                try:
                    await self.message.edit(embed=self.embed())
                    failures = 0
                except discord.NotFound:
                    break
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

    def embed(self) -> discord.Embed:
        if self.scream:
            emb = discord.Embed(color=0x000000)
            emb.set_image(url=f"attachment://{self.scream[0]}")
            return emb
        emb = self._base_embed()
        if self.picking == "layout":
            hint = f"🤔 {self.st.label('Layout')}: {LAYOUTS[self.layout][0]} • {self.st.label('pick one below, or use the arrows')}"
            emb.description = f"-# {hint}\n{emb.description or ''}"
        elif self.menu:
            lab = self.st.label
            summary = (
                f"{lab('font')} {self.st.label_name} · {lab('layout')} {LAYOUTS[self.layout][0]} · "
                f"{lab('tips')} {lab('on' if self.tips_playing else 'off')}"
            )
            emb.description = f"-# ☰ {lab('Menu')} • {lab('pick an action below')}\n-# {summary}\n{emb.description or ''}"
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
            f"> {self._dot(e)} **#{self.rank[e.id]}** · {self.st.obj(e.name)}{self._star(e)}\n"
            f"> {e.emoji_str} {self.st.label('Rarity')}: `{e.rarity:g}`  `{self.bar(e.rarity)}`"
        ).replace(">  ", "> ")
        if e.attack is not None and e.health is not None:
            text += f"\n> ⚔️ `{e.attack}`  ❤️ `{e.health}`"
        if e.ability:
            text += f"\n> *{self.th.bullet} {short(e.ability, 60)}*"
        return text

    def _compact_line(self, e: Entry) -> str:
        return f"{self._dot(e)} **#{self.rank[e.id]}** {e.emoji_str} {self.st.obj(e.name)}{self._star(e)} · `{e.rarity:g}`".replace("  ", " ")

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
                parts.append(f"{e.emoji_str} {self.st.label('Rarity')} `{e.rarity:g}` `{self.bar(e.rarity)}`".replace("  ", " ").strip())
                if e.attack is not None and e.health is not None:
                    parts.append(f"⚔️ `{e.attack}`  ❤️ `{e.health}`")
                if e.ability:
                    parts.append(f"*{self.th.bullet} {short(e.ability, 60)}*")
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
        if self.layout == "banner":
            return "\n".join(
                f"## {self._dot(e)} {self.st.obj(e.name)}{self._star(e)}\n"
                f"-# #{self.rank[e.id]} · {e.emoji_str} {self.st.label('Rarity')} `{e.rarity:g}` `{self.bar(e.rarity)}`".replace("  ", " ")
                for e in shown
            )
        if self.layout == "ledger":
            top = "┌─────┬────────────────────────┬─────────┐"
            mid = "├─────┼────────────────────────┼─────────┤"
            end = "└─────┴────────────────────────┴─────────┘"
            rows = [top, f"│ {'#':^3} │ {'Name':<22} │ {'Rarity':^7} │", mid]
            rows += [
                f"│ {self.rank[e.id]:>3} │ {short(e.name.replace('`', chr(39)), 22):<22} │ {e.rarity:>7g} │"
                for e in shown
            ]
            rows.append(end)
            return "```\n" + "\n".join(rows) + "\n```"
        if self.layout == "stack":
            lab = self.st.label
            return "\n\n".join(
                f"**#{self.rank[e.id]}** · {self.st.obj(e.name)}{self._star(e)}\n"
                f"{lab('rarest')} {self._strip_for(e)} {lab('common')}"
                for e in shown
            )
        if self.layout == "timeline":
            lines = []
            for i, e in enumerate(shown):
                if i:
                    lines.append("┃")
                lines.append(f"● **#{self.rank[e.id]}** {self.st.obj(e.name)}{self._star(e)} · `{e.rarity:g}`")
                if e.ability:
                    lines.append(f"┃  *{self.th.bullet} {short(e.ability, 60)}*")
            return "\n".join(lines)
        if self.layout == "sheet":
            blocks = []
            for e in shown:
                block = (
                    f"**#{self.rank[e.id]}** · {self.st.obj(e.name)}{self._star(e)}\n"
                    f"`RAR {e.rarity:>6g}` `{self.bar(e.rarity)}`"
                )
                if e.attack is not None and e.health is not None:
                    block += (
                        f"\n`ATK {e.attack:>4}` `{self.sbar(e.attack, self._max_atk, 6)}` "
                        f"`HP {e.health:>4}` `{self.sbar(e.health, self._max_hp, 6)}`"
                    )
                blocks.append(block)
            return "\n\n".join(blocks)
        if self.layout == "quote":
            blocks = []
            for e in shown:
                block = (
                    f"> ### {self._dot(e)} {self.st.obj(e.name)}{self._star(e)}\n"
                    f"> -# #{self.rank[e.id]} · {self.st.label('Rarity')} `{e.rarity:g}` `{self.bar(e.rarity)}`"
                )
                if e.ability:
                    block += f"\n> *{self.th.bullet} {short(e.ability, 60)}*"
                blocks.append(block)
            return "\n\n".join(blocks)
        if self.layout == "gauge":
            total = max(1, len(self.all))
            blocks = []
            for e in shown:
                pct = max(1, round(self.rank[e.id] / total * 100))
                blocks.append(
                    f"{self._dot(e)} **{self.st.obj(e.name)}**{self._star(e)} · `{e.rarity:g}`\n"
                    f"`{self.bar(e.rarity)}` {self.th.mark} {self.st.label('top')} `{pct}%`"
                )
            return "\n".join(blocks)
        if self.layout == "duo":
            names = [f"{self.rank[e.id]:>3} {short(e.name.replace('`', chr(39)), 12):<12} {e.rarity:>5g}" for e in shown]
            half = math.ceil(len(names) / 2)
            left, right = names[:half], names[half:]
            rows = ["┌" + "─" * 24 + "┬" + "─" * 24 + "┐"]
            for i in range(half):
                r = right[i] if i < len(right) else ""
                rows.append(f"│ {left[i]:<22} │ {r:<22} │")
            rows.append("└" + "─" * 24 + "┴" + "─" * 24 + "┘")
            return "```\n" + "\n".join(rows) + "\n```"
        if self.layout == "rail":
            blocks = []
            for e in shown:
                lines = [
                    f"▌ {self._dot(e)} **{self.st.obj(e.name)}**{self._star(e)} · #{self.rank[e.id]}",
                    f"▌ {e.emoji_str} `{e.rarity:g}` `{self.bar(e.rarity)}`".replace("  ", " "),
                ]
                if e.ability:
                    lines.append(f"▌ *{self.th.bullet} {short(e.ability, 60)}*")
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)
        if self.layout == "statline":
            lines = []
            for e in shown:
                stats = f" ⚔️`{e.attack}` ❤️`{e.health}`" if e.attack is not None and e.health is not None else ""
                lines.append(
                    f"{self._dot(e)} **#{self.rank[e.id]}** {self.st.obj(e.name)}{self._star(e)}{stats} {self.th.mark} `{e.rarity:g}`"
                )
            return "\n".join(lines)
        if self.layout == "plaque":
            left, right = self.th.card_frame
            blocks = []
            for e in shown:
                title = f"{left} {self.st.card(e.name)}{self._star(e)} {right}".strip()
                blocks.append(
                    f"**{title}**\n"
                    f"-# #{self.rank[e.id]} · {e.emoji_str} {self.st.label('Rarity')} `{e.rarity:g}` `{self.bar(e.rarity)}`".replace("  ", " ")
                )
            return f"\n{self.th.divider}\n".join(blocks)
        if self.layout == "heat":
            blocks = []
            for e in shown:
                hot = 10 - max(1, round(rarity_fraction(e.rarity) * 10)) + 1
                blocks.append(
                    f"**#{self.rank[e.id]}** · {self.st.obj(e.name)}{self._star(e)} · `{e.rarity:g}`\n"
                    f"{self._dot(e) * hot}{'⬛' * (10 - hot)}"
                )
            return "\n".join(blocks)
        if self.layout == "grid":
            return ""
        if self.layout == "spotlight" and shown:
            top, rest = shown[0], shown[1:]
            lines = [
                f"### {self._dot(top)} #{self.rank[top.id]} · {self.st.card(top.name)}",
                f"{top.emoji_str} {self.st.label('Rarity')}: `{top.rarity:g}`  `{self.bar(top.rarity)}`".replace("  `", " `").strip(),
            ]
            if top.attack is not None and top.health is not None:
                lines.append(f"⚔️ `{top.attack}`  ❤️ `{top.health}`")
            if top.ability:
                lines.append(f"*{self.th.bullet} {short(top.ability, 80)}*")
            if top.description:
                lines.append(f"> {short(top.description, 180)}")
            text = "\n".join(lines)
            if rest:
                text += f"\n{self.th.divider}\n" + "\n".join(self._compact_line(e) for e in rest)
            return text
        return "\n\n".join(self.entry_line(e) for e in shown)

    def list_embed(self) -> discord.Embed:
        body = self.render_body()
        page_text = f"{self.st.label('Page')} {self.page + 1}/{self.total_pages}  {page_bar(self.page, self.total_pages)}  •  "
        start = self.page * self.per_page
        end = min(start + self.per_page, len(self.entries))
        showing = f" • {self.st.label('Showing')} {start + 1}–{end}" if self.entries else ""

        if self.results_title:
            count = len(self.entries)
            embed = discord.Embed(
                title=f"🔎 {self.results_title}",
                description=(
                    f"-# {self.st.label('Found')} **{count}** {self.st.label('object' + ('s' if count != 1 else ''))} • "
                    f"{self.st.label('pick one below to see its ability')}{showing}\n\n{body}"
                ),
                color=SEARCH_COLOR,
            )
        else:
            embed = discord.Embed(
                title=self._frame(self.st.title(TITLE)),
                description=f"-# {self.st.label('Lower number = rarer')} • {len(self.all)} {self.st.label('objects')}{showing}\n{self._spectrum()}\n\n{body}",
                color=self._list_color(),
            )
        if getattr(self, "easter", None):
            embed.description = f"{embed.description}\n\n**{self.easter}**"
        if self.layout == "grid":
            for e in self.page_entries():
                embed.add_field(
                    name=short(f"{e.emoji_str} {self.st.obj(e.name)}".strip(), 250),
                    value=f"{self._dot(e)} #{self.rank[e.id]} · `{e.rarity:g}`\n`{self.bar(e.rarity)}`",
                    inline=True,
                )
        embed.set_footer(text=self.footer(page_text))
        self._author(embed)
        shown = self.page_entries()
        if shown and shown[0].emoji:
            embed.set_thumbnail(url=shown[0].emoji.url)
        return embed

    def top_players_embed(self) -> discord.Embed:
        label = PERIODS[self.top_period][0]
        embed = discord.Embed(
            title=f"👥 {self.st.label('Top catchers')} · {self.st.label(label)}", color=0xF1C40F
        )
        rows = self.top_players

        def who(discord_id: int) -> str:
            user = self.bot.get_user(discord_id)
            name = discord.utils.escape_markdown(user.display_name) if user else f"<@{discord_id}>"
            return name + (" ◀ you" if discord_id == self.author.id else "")

        if rows is None:
            embed.description = "Couldn't load the catches right now. Try again in a moment."
        elif not rows:
            embed.description = f"{self.th.divider}\n\nNobody caught anything here yet."
        else:
            top, count = rows[0]
            lines = [f"### 👑 {who(top)}", f"**{count}** {self.st.label('catches')}", self.th.divider]
            for i, (did, n) in enumerate(rows[1:], 2):
                lines.append(f"**#{i}** {who(did)} · `{n}` `{self.sbar(n, count)}`")
            lines.append(
                "-# hi verity"
                if self.top_method in ("original", "history")
                else "-# merry christmas"
            )
            embed.description = "\n".join(lines)
        embed.set_footer(text=self.footer())
        self._author(embed)
        return embed

    def top_embed(self) -> discord.Embed:
        if self.top_kind == "players":
            return self.top_players_embed()
        label = PERIODS[self.top_period][0]
        embed = discord.Embed(
            title=f"🏆 {self.st.label('Most caught')} · {self.st.label(label)}", color=0xF1C40F
        )
        rows = self.top_rows
        if rows is None:
            embed.description = "Couldn't load the catches right now. Try again in a moment."
        elif not rows:
            embed.description = f"{self.th.divider}\n\nNobody caught anything here yet."
        else:
            top, count = rows[0]
            lines = [
                f"### 👑 {self.st.card(top.name)}",
                f"{top.emoji_str} **{count}** {self.st.label('catches')}".strip(),
                self.th.divider,
            ]
            for i, (e, n) in enumerate(rows[1:], 2):
                lines.append(
                    f"{self._dot(e)} **#{i}** {e.emoji_str} {self.st.obj(e.name)} · `{n}` `{self.sbar(n, count)}`".replace("  ", " ")
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
            title=self._frame(f"{e.emoji_str} {self.st.card(e.name)}".replace("  ", " "), card=True),
            description=self._detail_description(e),
            color=gradient_color(self._rank_t(e)),
        )
        embed.add_field(
            name=self.st.label("Rarity"), value=f"`{e.rarity:g}`\n`{self.bar(e.rarity)}`", inline=True
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
                    f"`ATK {e.attack:>4}` `{self.sbar(e.attack, self._max_atk)}`\n"
                    f"`HP  {e.health:>4}` `{self.sbar(e.health, self._max_hp)}`"
                ), inline=True
            )

        ability_name = e.ability or "Melody wheres the name of this."
        ability_desc = e.description or "THe fuck? Wheres the ability desc Melody???"
        embed.add_field(
            name="\u200b",
            value=f"### ✨ {short(ability_name, 150)}\n>>> {short(ability_desc, 800)}",
            inline=False,
        )
        if e.emoji:
            embed.set_thumbnail(url=e.emoji.url)
        embed.set_footer(text=self.footer())
        self._author(embed)
        return embed

    def refresh(self):
        """Rebuild the buttons/selects for the current state."""
        self.clear_items()
        if self.scream:
            button = discord.ui.Button(label=SCREAM_LABEL, style=discord.ButtonStyle.secondary, row=0)
            button.callback = self._guard(self._search)
            self.add_item(button)
            return
        if self.picking:
            {"layout": self._add_layout_picker}.get(self.picking, self._add_font_picker)()
            return
        if self.menu:
            self._add_menu()
            return
        if self.top_period:
            select = discord.ui.Select(
                placeholder=self.st.label("Time range"),
                options=[
                    discord.SelectOption(label=name, value=key, default=(key == self.top_period))
                    for key, (name, _) in PERIODS.items()
                ],
                row=0,
            )
            select.callback = self._guard(self._top_select)
            self.add_item(select)
            self._add_button("Back to list", self._top_close, self._pri(), "↩️", row=1)
            self._add_button(
                "Top balls" if self.top_kind == "players" else "Top players",
                self._top_kind_toggle,
                discord.ButtonStyle.secondary,
                "🐾" if self.top_kind == "players" else "👥",
                row=1,
            )
            self._add_button("Quit", self._quit, discord.ButtonStyle.danger, "💔", row=1)
            return
        if self.focus:
            self._add_button(None, self._prev_obj, discord.ButtonStyle.secondary, "◀️", row=0)
            self._add_button("Back to list", self._back, self._pri(), "↩️", row=0)
            self._add_button(None, self._next_obj, discord.ButtonStyle.secondary, "▶️", row=0)
            self._add_button("Search", self._search, discord.ButtonStyle.success, "🔎", row=0)
            self._add_button("☰ Menu", self._open_menu, discord.ButtonStyle.secondary, row=0)
            return

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

        if self.entries:
            no_prev = self.page <= 0
            no_next = self.page >= self.total_pages - 1
            self._add_button("Back", self._prev, self._pri(), "⬅️", row=1, disabled=no_prev)
            self._add_button(
                f"{self.page + 1}/{self.total_pages}",
                self._jump,
                discord.ButtonStyle.secondary,
                row=1,
                disabled=self.total_pages <= 1,
            )
            self._add_button("Next", self._next, self._pri(), "➡️", row=1, disabled=no_next)
        self._add_button("Search", self._search, discord.ButtonStyle.success, "🔎", row=1)
        self._add_button("☰ Menu", self._open_menu, discord.ButtonStyle.secondary, row=1)

        if self.results_title:
            self._add_button("Clear", self._clear, discord.ButtonStyle.secondary, "✖️", row=2)
    def _add_menu(self):
        inlist = not self.focus
        opts: list[tuple[str, str, str, str]] = []
        if inlist and self.entries:
            opts.append(("sort", SORT_LABELS[self.sort_mode], "Change the sort order", "↕️"))
        if self.entries or self.all:
            opts.append(("random", "Random", "Open a random object", "🎲"))
        if inlist and self.total_pages > 1:
            opts.append(("jump", "Jump to page", "Type a page number", "🔢"))
            opts.append(("first", "First page", "Go to the rarest page", "⏮️"))
            opts.append(("last", "Last page", "Go to the last page", "⏭️"))
        if inlist and self.results_title:
            opts.append(("clear", "Clear", "Back to the full list", "✖️"))
        opts.append(("daily", "Object of the day", "Today's featured object", "🌟"))
        opts.append(("top", "Top catches", "Most caught balls and top players", "🏆"))
        opts.append(("font", f"Font: {self.st.label_name}", f"Pick from {len(FONTS) - 1} fonts", "🔤"))
        if inlist:
            opts.append(("layout", f"Layout: {LAYOUTS[self.layout][0]}", f"Pick from {len(LAYOUTS)} layouts", "🤔"))
        if self.has_zero:
            opts.append(
                ("hide", f"Hide 0.0: {'On' if self.hide_zero else 'Off'}", "Hide objects with rarity 0.0 or lower", "😂")
            )
        opts.append(("quit", "Quit", "Close this menu for good", "💔"))
        select = discord.ui.Select(
            placeholder=self.st.label("Pick an action"),
            options=[
                discord.SelectOption(label=label, value=value, description=desc, emoji=emoji)
                for value, label, desc, emoji in opts
            ],
            row=0,
        )
        select.callback = self._guard(self._menu_pick)
        self.add_item(select)
        self._add_button("Close menu", self._close_menu, self._pri(), "↩️", row=1)

    async def _open_menu(self, interaction: discord.Interaction):
        self.menu = True
        await self.update(interaction)

    async def _close_menu(self, interaction: discord.Interaction):
        self.menu = False
        await self.update(interaction)

    async def _menu_pick(self, interaction: discord.Interaction):
        choice = interaction.data["values"][0]
        self.menu = False
        handler = {
            "sort": self._sort,
            "random": self._random,
            "jump": self._jump,
            "first": self._first,
            "last": self._last,
            "clear": self._clear,
            "top": self._top_open,
            "daily": self._daily_open,
            "font": self._font,
            "layout": self._layout,
            "hide": self._hide_zero,
            "quit": self._quit,
        }.get(choice)
        if handler:
            await handler(interaction)
        else:
            await self.update(interaction)

    async def _top_select(self, interaction: discord.Interaction):
        period = interaction.data["values"][0]
        await interaction.response.defer()
        await self._load_top(period if period in PERIODS else "day")
        await self.update(interaction)

    def _add_font_picker(self):
        keys = list(FONTS)
        chunks = math.ceil(len(keys) / 25)
        size = math.ceil(len(keys) / chunks)
        for c in range(chunks):
            part = keys[c * size : (c + 1) * size]
            select = discord.ui.Select(
                placeholder=self.st.label(
                    "Choose a font" if chunks == 1 else f"Choose a font · {c * size + 1}–{c * size + len(part)}"
                ),
                options=[
                    discord.SelectOption(
                        label=FONTS[key].label_name,
                        value=key,
                        description=short(FONTS[key].obj("The Codex Archives"), 100),
                        default=(key == self.font),
                    )
                    for key in part
                ],
                row=c,
            )
            select.callback = self._guard(self._pick_font)
            self.add_item(select)
        self._add_button(None, self._font_prev, discord.ButtonStyle.secondary, "◀️", row=chunks)
        self._add_button("Done", self._font_done, discord.ButtonStyle.success, "✅", row=chunks)
        self._add_button(None, self._font_next, discord.ButtonStyle.secondary, "▶️", row=chunks)

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
                try:
                    note = "Something broke on my end. Try again in a moment."
                    if interaction.response.is_done():
                        await interaction.followup.send(note, ephemeral=True)
                    else:
                        await interaction.response.send_message(note, ephemeral=True)
                except Exception:
                    pass
            finally:
                self._busy = False
                self._last_action = time.monotonic()

        return wrapper

    async def _select(self, interaction: discord.Interaction):
        chosen = int(interaction.data["values"][0])
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

    async def _font(self, interaction: discord.Interaction):
        self.picking = "font"
        await self.update(interaction)

    def _set_font(self, key: str):
        if key in FONTS:
            self.font = key
            self.prefs.set(self.author.id, "font", key)

    async def _pick_font(self, interaction: discord.Interaction):
        self._set_font(interaction.data["values"][0])
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
        self._set_layout(interaction.data["values"][0])
        await self.update(interaction)

    async def _layout_step(self, interaction: discord.Interaction, delta: int):
        keys = list(LAYOUTS)
        self._set_layout(keys[(keys.index(self.layout) + delta) % len(keys)])
        await self.update(interaction)

    async def _layout_prev(self, interaction: discord.Interaction):
        await self._layout_step(interaction, -1)

    async def _layout_next(self, interaction: discord.Interaction):
        await self._layout_step(interaction, 1)

    async def _daily_open(self, interaction: discord.Interaction):
        daily = self.daily
        if daily and daily.id in self.rank:
            self.focus = daily
        await self.update(interaction)

    async def _load_top(self, period: str):
        self.top_period = period
        try:
            if self.top_kind == "players":
                self.top_players, self.top_method = await top_catchers(period)
                return
            rows = []
            for ball_id, total in await top_catches(period):
                e = self.by_id.get(ball_id)
                if e is None or (self.hide_zero and e.rarity <= 0):
                    continue
                rows.append((e, total))
            self.top_rows = rows[:10]
        except Exception:
            log.exception("Most-caught lookup failed")
            if self.top_kind == "players":
                self.top_players = None
            else:
                self.top_rows = None

    async def _top_kind_toggle(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.top_kind = "players" if self.top_kind == "balls" else "balls"
        await self._load_top(self.top_period or "day")
        await self.update(interaction)

    def _period_cb(self, period: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
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
        self.apply_search(self.results_query)
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

    async def _quit(self, interaction: discord.Interaction):
        self.stop()
        self.stop_ticker()
        await interaction.response.defer()
        await interaction.delete_original_response()

    def _button_key(self, interaction: discord.Interaction) -> str | None:
        cid = (getattr(interaction, "data", None) or {}).get("custom_id")
        for item in self.children:
            if cid is not None and getattr(item, "custom_id", None) == cid:
                return f"{getattr(item, 'label', None)}|{getattr(item, 'emoji', None)}|{getattr(item, 'placeholder', None)}"
        return None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            now = time.monotonic()
            if interaction.user.id != self.author.id:
                if now - self._stranger_at > 3:
                    self._stranger_at = now
                    try:
                        await interaction.response.send_message("Who are YOU??", ephemeral=True)
                    except (discord.HTTPException, discord.InteractionResponded):
                        pass
                return False
            if now < self._locked_until:
                return False
            key = self._button_key(interaction)
            same_button_too_soon = key is not None and now - self._button_at.get(key, 0.0) < BUTTON_COOLDOWN
            if (
                (self._busy and now - self._last_action < 10)
                or now - self._last_action < CLICK_COOLDOWN
                or same_button_too_soon
            ):
                self._ignored = [t for t in self._ignored if now - t < 5] + [now]
                try:
                    if len(self._ignored) >= SPAM_CLICKS:
                        self._ignored.clear()
                        self._locked_until = now + SPAM_LOCK_SECONDS
                        await interaction.response.send_message(
                            f"Whoa, too many clicks. The buttons are paused for {SPAM_LOCK_SECONDS} seconds.", ephemeral=True
                        )
                    elif now - self._last_slow > 6:
                        self._last_slow = now
                        await interaction.response.send_message("Easy there, one click at a time.", ephemeral=True)
                    elif now - self._last_defer > 0.75:
                        self._last_defer = now
                        await interaction.response.defer()
                except (discord.HTTPException, discord.InteractionResponded):
                    pass
                return False
            self._busy = True
            self._last_action = now
            if key is not None:
                if len(self._button_at) > 60:
                    self._button_at.clear()
                self._button_at[key] = now
            return True
        except Exception:
            log.exception("Rarity click check failed")
            self._busy = False
            return False

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
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class Rarity(commands.Cog):
    """Paginated rarity list with search, suggestions, ability cards and rotating tips."""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.prefs = PrefStore()
        self.active: weakref.WeakValueDictionary[int, RarityView] = weakref.WeakValueDictionary()
        self._opening: set[int] = set()

    async def cog_unload(self):
        for view in list(self.active.values()):
            view.stop()
            view.stop_ticker()
        self.prefs.flush()

    @app_commands.command(name="rarity", description="Show the rarity list of every object")
    @app_commands.describe(name="Jump straight to an object (suggestions appear as you type)")
    async def rarity(self, interaction: discord.Interaction, name: str | None = None):
        if interaction.user.id in self._opening:
            try:
                await interaction.response.send_message("Already opening it, one moment.", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        self._opening.add(interaction.user.id)
        try:
            await self._open_list(interaction, name)
        except Exception:
            log.exception("rarity command failed")
            try:
                note = "Something broke on my end. Try again in a moment."
                if interaction.response.is_done():
                    await interaction.followup.send(note, ephemeral=True)
                else:
                    await interaction.response.send_message(note, ephemeral=True)
            except Exception:
                pass
        finally:
            self._opening.discard(interaction.user.id)

    async def _open_list(self, interaction: discord.Interaction, name: str | None):
        old = self.active.get(interaction.user.id)
        if old is not None:
            await old.close()

        entries = load_entries(self.bot)
        view = RarityView(self.bot, interaction.user, entries, self.prefs)

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
        try:
            entries = load_entries(self.bot)
            if self.prefs.get(interaction.user.id, "hide_zero", HIDE_ZERO_DEFAULT):
                entries = [e for e in entries if e.rarity > 0] or entries
            if current.strip():
                matches, suggestions = await asyncio.wait_for(
                    asyncio.to_thread(search_entries, entries, current), 2.0
                )
                results = matches or suggestions
            else:
                results = entries
        except Exception:
            return []
        return [
            app_commands.Choice(
                name=short(f"{e.name} • Rarity {e.rarity:g}", 100),
                value=short(e.name, 100),
            )
            for e in results[:25]
        ]
