"""Telegram emoji pack: every hero ability as a 100×100 custom emoji.

Each emoji is our post template in miniature: a printed photo card taped to the chat. From the outside in:
a dark ink line, a paper rim, a frame in the hero's colour, a near-black inside with a dry-brush stroke,
and the game's ability icon printed on top. The four cards of a hero lean at four different angles; the
ultimate (slot 4) gets a second strip of tape on its bottom corner.

The double edge (ink line + paper rim) is what lets one design work on every Telegram theme: on a dark
chat the rim stands out, on a light one the line, on the mid-tone default wallpaper both. `--check` measures
it against Telegram's backgrounds. The frame keeps the hero's own colour (bordeaux stays dark, yellow
stays light); its tones are derived in CIELAB from one PALETTE entry per hero.

Reads data/heroes/index.json and the ability icons in data/heroes/abilities/ (see backend/heroes.py),
writes data/emoji/<hero>-<slot>-<ability>.png and data/emoji/index.json. All noise is seeded, so the same
icons and palette give the same pixels.

    python -m backend.emoji            # draw the whole pack
    python -m backend.emoji --check    # then test every emoji's edge against Telegram's backgrounds

Needs only pillow.
"""
import json
import math
import random
import re
import sys
from statistics import median

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config
from .media import log

HEROES = config.ROOT / "data" / "heroes"
OUT = config.ROOT / "data" / "emoji"
SIZE = 100  # Telegram's custom emoji: exactly 100×100
K = 4  # drawn at 4× and scaled down
S = SIZE * K
PAPER = (243, 240, 232)  # warm off-white, like printed paper
TAPE = (206, 201, 186)
LEAN = [-4, 3, -2.5, 4.5]  # degrees, by ability slot

# One colour per hero, picked from the hero's look; the game's m_colorUI is only a hint (many heroes share
# placeholder values). "#frame/#stroke" when a hero reads as two colours.
PALETTE = {
    "atlas": "#2f86a3",        # Abrams: blue-teal skin
    "fencer": "#931a2a",       # Apollo: bordeaux
    "baba": "#7d3d70",         # Baba: plum coat
    "bebop": "#c96a2c",        # Bebop: copper robot
    "punkgoat": "#b5338e",     # Billy: punk magenta
    "nano": "#6a3fd0",         # Calico: violet
    "unicorn": "#8f8bec",      # Celeste: periwinkle hair
    "deadpack": "#d2a12c",     # Deadman Danny: yellow raincoat
    "drifter": "#a3221f",      # Drifter: blood-red jacket
    "dynamo": "#2f5bd1",       # Dynamo: cobalt suit
    "necro": "#7aa594",        # Graves: grave jade
    "orion": "#4f9c6e",        # Grey Talon: sage
    "haze": "#b27b30",         # Haze: ochre
    "astro": "#b4573a",        # Holliday: terracotta leather
    "inferno": "#ec5f24",      # Infernus: flame
    "tengu": "#a774d6",        # Ivy: lilac
    "kelvin": "#5fb5d8",       # Kelvin: ice blue
    "ghost": "#1c9467",        # Lady Geist: emerald
    "lash": "#5c6878",         # Lash: gunmetal
    "forge": "#3f6aa8",        # McGinnis: work-jacket blue
    "vampirebat": "#b0284f",   # Mina: crimson
    "mirage": "#b89254",       # Mirage: desert sand
    "krill": "#6b5490",        # Mo & Krill: dusty purple
    "nurse": "#3a9f95",        # Nurse Harrow: scrubs teal
    "bookworm": "#9dbf3a",     # Paige: lime sweater
    "chrono": "#c4507a",       # Paradox: hourglass pink
    "synth": "#5a5f9e",        # Pocket: slate indigo
    "ratking": "#8a6b4c",      # Rat King: sewer brown
    "familiar": "#6a9ee6",     # Rem: night-sky hat
    "gigawatt": "#e8c03a",     # Seven: electric yellow
    "shiv": "#3a3532/#cf2f3c", # Shiv: black pompadour, blood
    "werewolf": "#9aabbf",     # Silver: moonlight
    "magician": "#4a3fb0",     # Sinclair: stage indigo
    "chessmaster": "#a0963f",  # Solomon: olive brass
    "doorman": "#7f3428",      # The Doorman: hotel brick
    "priest": "#5f3f8f",       # Venator: violet hair
    "frank": "#6f9a4c",        # Victor: galvanic moss
    "hornet": "#5b6bd0",       # Vindicta: night blue
    "artist": "#c82f43",       # Violet: red beret
    "viscous": "#7cc24a",      # Viscous: slime
    "viper": "#b6c22e",        # Vyper: venom
    "warden": "#2c4170",       # Warden: police navy
    "wraith": "#9a4d82",       # Wraith: mauve
    "yamato": "#4f8577",       # Yamato: jade steel
}
NO_COLOUR = "#8a8f98"  # a new hero before it gets a colour here


# ── Colour ─────────────────────────────────────────────

def rgb(hexcol: str) -> tuple[int, int, int]:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4


def _gam(c: float) -> int:
    c = c * 12.92 if c <= .0031308 else 1.055 * c ** (1 / 2.4) - .055
    return round(min(1, max(0, c)) * 255)


def luminance(c) -> float:
    r, g, b = (_lin(v) for v in c)
    return .2126 * r + .7152 * g + .0722 * b


def to_lch(c) -> tuple[float, float, float]:
    r, g, b = (_lin(v) for v in c)
    x = (.4124 * r + .3576 * g + .1805 * b) / .95047
    y = .2126 * r + .7152 * g + .0722 * b
    z = (.0193 * r + .1192 * g + .9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116
    L, a, bb = 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))
    return L, math.hypot(a, bb), math.atan2(bb, a)


def from_lch(L: float, C: float, h: float) -> tuple[int, int, int]:
    """LCh -> sRGB, chroma reduced until the colour fits the gamut."""
    inv = lambda t: t ** 3 if t ** 3 > .008856 else (t - 16 / 116) / 7.787
    for _ in range(40):
        a, bb = C * math.cos(h), C * math.sin(h)
        fy = (L + 16) / 116
        x, y, z = inv(fy + a / 500) * .95047, inv(fy), inv(fy - bb / 200) * 1.08883
        r = 3.2406 * x - 1.5372 * y - .4986 * z
        g = -.9689 * x + 1.8758 * y + .0415 * z
        b = .0557 * x - .2040 * y + 1.0570 * z
        if min(r, g, b) >= -.001 and max(r, g, b) <= 1.001:
            break
        C *= .93
    return (_gam(r), _gam(g), _gam(b))


def tones(entry: str) -> dict[str, tuple[int, int, int]]:
    """The frame stays close to the hero's colour, extremes pulled a little toward the middle; the stroke is
    one step lighter so it reads on the black inside, but never so light that the paper icon sinks into it.
    Contrast with the chat is the double edge's job, not the frame's."""
    frame, _, stroke = entry.partition("/")
    L, C, h = to_lch(rgb(frame))
    sL, sC, sh = to_lch(rgb(stroke)) if stroke else (L + 14, C, h)
    return {"frame": from_lch(L * .72 + 46 * .28, C * .92, h),
            "stroke": from_lch(min(68, max(50, sL)), sC, sh),
            "core": from_lch(6.5, min(C, 30) * .35, h),
            "ink": from_lch(9, min(C, 30) * .5, h)}


# ── Drawing helpers (coordinates in emoji pixels, drawn at K×) ──

def noise(size: tuple[int, int], sigma: float, seed: int) -> Image.Image:
    """Gaussian noise around 128, seeded so a re-run gives the same pixels."""
    rnd = random.Random(seed)
    w, h = size
    return Image.frombytes("L", size, bytes(min(255, max(0, round(128 + rnd.gauss(0, sigma)))) for _ in range(w * h)))


def grain(img: Image.Image, sigma: float, seed: int = 1) -> Image.Image:
    """Film grain on the colour channels, alpha untouched."""
    r, g, b, a = img.split()
    n = noise(img.size, sigma, seed)
    return Image.merge("RGBA", (*(ImageChops.add(ch, n, 1.0, -128) for ch in (r, g, b)), a))


def brush(mask: Image.Image, angle: float, seed: int) -> Image.Image:
    """Dry-brush edges: the mask softened, shaken by noise stretched along the stroke, cut again."""
    streaks = noise((S * 2 // 14, S * 2 // 3), 75, seed).resize((S * 2, S * 2), Image.BICUBIC)
    streaks = streaks.rotate(angle, resample=Image.BICUBIC).crop((S // 2, S // 2, S // 2 + S, S // 2 + S))
    m = ImageChops.add(mask.filter(ImageFilter.GaussianBlur(4)), streaks, 1.0, -128)
    return m.point(lambda v: 255 if v > 127 else 0)


def solid(color, mask: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", mask.size, (*color, 255))
    layer.putalpha(mask)
    return layer


def over(base: Image.Image, layer: Image.Image) -> None:
    base.alpha_composite(layer)


def polygon(points) -> Image.Image:
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon([(x * K, y * K) for x, y in points], fill=255)
    return m


def rect(x0: float, y0: float, x1: float, y1: float) -> Image.Image:
    return polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def shifted(mask: Image.Image, dx: float, dy: float) -> Image.Image:
    return ImageChops.offset(mask, round(dx * K), round(dy * K))


def band(p0, p1, width: float) -> tuple[Image.Image, float]:
    """A straight band from p0 to p1 and its angle."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    nx, ny = -dy / math.hypot(dx, dy) * width / 2, dx / math.hypot(dx, dy) * width / 2
    return (polygon([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]),
            math.degrees(math.atan2(dy, dx)))


def icon_mask(icon: str, box: float) -> Image.Image:
    """The ability icon's shape (white + alpha in the game), its 137 px square scaled to `box`, centred."""
    px = round(box * K)
    a = Image.open(HEROES / "abilities" / icon).convert("RGBA").getchannel("A").resize((px, px), Image.LANCZOS)
    m = Image.new("L", (S, S), 0)
    m.paste(a, ((S - px) // 2, (S - px) // 2))
    return m


def tape(img: Image.Image, cx: float, cy: float, ang: float, w: float = 34, h: float = 11, seed: int = 0) -> None:
    """A strip of matte tape with torn short ends, dense enough and edged to show on white and on black."""
    rnd = random.Random(seed)
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2)]
    pts += [(w / 2 + rnd.uniform(-1.3, 1.3), -h / 2 + h * (i + 1) / 6) for i in range(5)]
    pts += [(w / 2, h / 2), (-w / 2, h / 2)]
    pts += [(-w / 2 + rnd.uniform(-1.3, 1.3), h / 2 - h * (i + 1) / 6) for i in range(5)]
    a = math.radians(ang)
    m = polygon([(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts])
    over(img, solid((0, 0, 0), shifted(m, .4, .9).filter(ImageFilter.GaussianBlur(K * .6)).point(lambda v: round(v * .22))))
    edge = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(int(K * .7) * 2 + 1)))
    over(img, solid(TAPE, m.point(lambda v: round(v * .74))))
    over(img, solid((120, 116, 104), edge.point(lambda v: round(v * .35))))


# ── The emoji ──────────────────────────────────────────

LINE, RIM, FRAME = 1.6, 2.2, 5.0  # ink line (thinner smears to half-tone once the card leans), paper rim, frame
CARD = (11, 12, 89, 90)


def draw(icon: str, t: dict, slot: int, seed: int) -> Image.Image:
    x0, y0, x1, y1 = CARD
    card = rect(x0, y0, x1, y1)
    inside = rect(x0 + RIM + FRAME, y0 + RIM + FRAME, x1 - RIM - FRAME, y1 - RIM - FRAME)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    over(layer, solid(t["ink"], card.filter(ImageFilter.MaxFilter(round(LINE * K) * 2 + 1))))
    over(layer, solid(PAPER, card))
    over(layer, solid(t["frame"], rect(x0 + RIM, y0 + RIM, x1 - RIM, y1 - RIM)))
    over(layer, solid(t["core"], inside))
    stroke, angle = band((4, 24), (96, 70), 25)
    over(layer, solid(t["stroke"], ImageChops.multiply(brush(stroke, -angle, seed), inside)))
    g = ImageChops.multiply(icon_mask(icon, SIZE - 2 * (x0 + RIM + FRAME) + 2), inside)
    halo = ImageChops.lighter(g.filter(ImageFilter.MaxFilter(7)), shifted(g, 1.5, 1.5))  # thin ink line + print offset
    over(layer, solid(t["ink"], ImageChops.multiply(halo, inside)))
    over(layer, solid(PAPER, g))
    lean = LEAN[(slot - 1) % 4]
    layer = layer.rotate(lean, resample=Image.BICUBIC, center=(S / 2, S / 2))
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow = layer.getchannel("A").filter(ImageFilter.GaussianBlur(K * 1.6)).point(lambda v: round(v * .38))
    over(img, solid((0, 0, 0), shifted(shadow, 1.2, 2.4)))
    over(img, layer)
    tape(img, 50 + lean * .7, 13, -lean * 1.5 - 2, seed=seed + slot)
    if slot == 4:  # ultimate: a second strip holds the bottom corner
        tape(img, 84, 85, -45 + lean, w=24, h=9.5, seed=seed + 9)
    return grain(img.resize((SIZE, SIZE), Image.LANCZOS), 5)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("'", "")).strip("-")


# ── Edge check ─────────────────────────────────────────

# Where emoji end up: message bubbles, and the bare chat background for a message of emoji only.
BACKGROUNDS = {
    "night chat": (14, 22, 33), "night incoming": (24, 37, 51), "night outgoing": (43, 82, 120),
    "black (iOS night, AMOLED)": (0, 0, 0), "day incoming": (255, 255, 255), "day outgoing": (239, 253, 222),
    "default wallpaper, light": (219, 221, 187), "default wallpaper, mid": (136, 184, 132),
    "default wallpaper, green": (107, 165, 135),
}
MIN_CONTRAST = 3.0  # WCAG's threshold for shapes


def edge_contrast(emoji: Image.Image, bg) -> float:
    """The best of the outer four 1 px rings of the silhouette: median contrast ratio against `bg`.
    A double edge passes when either of its lines stands out."""
    comp = Image.new("RGBA", emoji.size, (*bg, 255))
    comp.alpha_composite(emoji)
    px, lb = comp.load(), luminance(bg)
    ratio = lambda c: (max(luminance(c), lb) + .05) / (min(luminance(c), lb) + .05)
    cur, best = emoji.getchannel("A").point(lambda v: 255 if v >= 200 else 0), 0.0
    for _ in range(4):
        nxt = cur.filter(ImageFilter.MinFilter(3))
        ring = ImageChops.subtract(cur, nxt).load()
        vals = [ratio(px[x, y][:3]) for y in range(emoji.height) for x in range(emoji.width) if ring[x, y]]
        if len(vals) > 20:
            best = max(best, median(vals))
        cur = nxt
    return best


def check(files) -> int:
    failed = 0
    for name, bg in BACKGROUNDS.items():
        scores = sorted((edge_contrast(Image.open(f), bg), f.stem) for f in files)
        bad = [s for s in scores if s[0] < MIN_CONTRAST]
        failed += len(bad)
        log(f"  {name:26} worst {scores[0][0]:4.1f}:1 ({scores[0][1]})" + (f", {len(bad)} below {MIN_CONTRAST}:1" if bad else ""))
    return 1 if failed else 0


# ── Main ───────────────────────────────────────────────

def main() -> int:
    index = json.loads((HEROES / "index.json").read_text("utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    entries, written = [], set()
    for h in sorted(index["heroes"], key=lambda h: h["name"]):
        if h["key"] not in PALETTE:
            log(f"no colour for {h['name']} ({h['key']}): drawn grey, add it to PALETTE")
        t = tones(PALETTE.get(h["key"], NO_COLOUR))
        for a in h["abilities"]:
            name = f"{slug(h['name'])}-{a['slot']}-{slug(a['name'])}.png"
            draw(a["icon"], t, a["slot"], seed=h["id"]).save(OUT / name, optimize=True)
            written.add(name)
            entries.append({"hero": h["key"], "hero_name": h["name"], "slot": a["slot"],
                            "ability": a["key"], "name": a["name"], "file": name})
    for old in OUT.glob("*.png"):
        if old.name not in written:
            old.unlink()
    (OUT / "index.json").write_text(json.dumps({"build": index["build"], "emoji": entries},
                                               ensure_ascii=False, indent=1) + "\n", "utf-8")
    log(f"build {index['build']}: {len(entries)} emoji in {OUT.relative_to(config.ROOT)}")
    if "--check" in sys.argv:
        log("edge contrast against Telegram's backgrounds:")
        return check(sorted(OUT.glob("*.png")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
