"""Text symbols for the emoji packs: arrows, dots, dashes, quotes and other marks for channel posts.

Each symbol is the mark itself cut out of paper in our green, with no tile behind it: strips of paper with
their ends cut at a slant and every fold cut straight across, a dry-brush stroke of lighter green over it,
the same paper rim and ink line as the hero and item emoji (so it reads on every Telegram theme), and an
off-register second print in deep green. Every corner is nudged a little, by a seed from the symbol's name,
so the cuts look made by hand and come out the same on every run.

Shapes are drawn in emoji pixels (0–100); backend/emoji.py renders them into data/emoji/symbols/.
"""
import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .emoji import K, LINE, PAPER, RIM, S, SIZE, band, brush, grain, grow, over, solid

GREENS = {"fill": (36, 138, 98), "light": (96, 196, 146), "deep": (20, 70, 52), "ink": (8, 28, 20)}
REDS = {"fill": (186, 58, 43), "light": (236, 118, 94), "deep": (94, 26, 20), "ink": (30, 8, 6)}  # the site's --danger
# A symbol drawn exactly like another one (same cuts, lean and brush), in another colour.
VARIANTS = {"arrow-down-red": ("arrow-down", REDS)}
W = 15  # the width of a strip, the same for every symbol so they sit together in a line of text


# ── Paper strips ───────────────────────────────────────

def bar(p0, p1, w, cut0=0.0, cut1=0.0):
    """A strip from p0 to p1, w wide, its ends cut at cut0 / cut1 degrees."""
    (x0, y0), (x1, y1) = p0, p1
    length = math.hypot(x1 - x0, y1 - y0)
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -dy * w / 2, dx * w / 2
    t0, t1 = math.tan(math.radians(cut0)) * w / 2, math.tan(math.radians(cut1)) * w / 2
    return [(x0 + nx + dx * t0, y0 + ny + dy * t0), (x1 + nx + dx * t1, y1 + ny + dy * t1),
            (x1 - nx - dx * t1, y1 - ny - dy * t1), (x0 - nx - dx * t0, y0 - ny - dy * t0)]


def hull(points):
    pts = sorted(set(points))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def path(points, w, cut0=0.0, cut1=0.0):
    """A strip folded along points: one bar per segment, each fold cut straight across."""
    bars = [bar(points[i], points[i + 1], w, cut0 if i == 0 else 0, cut1 if i == len(points) - 2 else 0)
            for i in range(len(points) - 1)]
    return bars + [hull([bars[i][1], bars[i][2], bars[i + 1][0], bars[i + 1][3], points[i + 1]]) for i in range(len(bars) - 1)]


def blob(cx, cy, r, sides, seed):
    """A dot cut with scissors: a polygon with a few uneven sides."""
    rnd = random.Random(seed)
    a0 = rnd.uniform(0, math.pi)
    return [[(cx + r * rnd.uniform(.9, 1.06) * math.cos(a0 + 2 * math.pi * i / sides),
              cy + r * rnd.uniform(.9, 1.06) * math.sin(a0 + 2 * math.pi * i / sides)) for i in range(sides)]]


def ring(cx, cy, r, w, sides, seed):
    rnd = random.Random(seed)
    a0 = rnd.uniform(0, math.pi)
    return path([(cx + r * math.cos(a0 + 2 * math.pi * i / sides), cy + r * math.sin(a0 + 2 * math.pi * i / sides))
                 for i in range(sides + 2)], w)


def rotate(shapes, deg, c=(50, 50)):
    a = math.radians(deg)
    return [[(c[0] + (x - c[0]) * math.cos(a) - (y - c[1]) * math.sin(a),
              c[1] + (x - c[0]) * math.sin(a) + (y - c[1]) * math.cos(a)) for x, y in s] for s in shapes]


def arrow(deg, length=72):
    half = length / 2
    return rotate([bar((50 - half, 50), (50 + half - 22, 50), W, cut0=-12),
                   [(50 + half - 26, 26), (50 + half + 2, 51), (50 + half - 24, 75)]], deg)


def chevrons(right):
    s = path([(31, 28), (49, 50), (31, 72)], 12.5) + path([(53, 28), (71, 50), (53, 72)], 12.5)
    return s if right else [[(100 - x, y) for x, y in p] for p in s]


def comma(x, y, flip=False):
    shape = [(x, y), (x + 15, y + 1), (x + 15, y + 15), (x + 7, y + 30), (x + 1, y + 30), (x + 5, y + 15), (x, y + 14)]
    return [[(2 * x + 15 - px, 2 * y + 30 - py) for px, py in shape] if flip else shape]


def star(points, outer, inner, cy=50):
    n = points * 2
    return [[(50 + (outer if i % 2 == 0 else inner) * math.cos(math.radians(-90 + 360 / n * i)),
              cy + (outer if i % 2 == 0 else inner) * math.sin(math.radians(-90 + 360 / n * i))) for i in range(n)]]


# ── The symbols, in the pack's order: key → (English, Russian, shapes) ──

SYMBOLS = {
    "arrow-right": ("arrow right", "стрелка вправо", lambda: arrow(0)),
    "arrow-left": ("arrow left", "стрелка влево", lambda: arrow(180)),
    "arrow-up": ("arrow up", "стрелка вверх", lambda: arrow(-90)),
    "arrow-down": ("arrow down", "стрелка вниз", lambda: arrow(90)),
    "arrow-down-red": ("arrow down red", "стрелка вниз красная", lambda: arrow(90)),
    "arrow-up-right": ("arrow up right", "стрелка вверх вправо", lambda: arrow(-45, 66)),
    "arrow-down-right": ("arrow down right", "стрелка вниз вправо", lambda: arrow(45, 66)),
    "arrow-down-left": ("arrow down left", "стрелка вниз влево", lambda: arrow(135, 66)),
    "arrow-up-left": ("arrow up left", "стрелка вверх влево", lambda: arrow(-135, 66)),
    "dot-center": ("middle dot", "точка по центру", lambda: blob(50, 52, 10, 7, 1)),
    "bullet": ("bullet", "кружок", lambda: blob(50, 50, 19, 9, 2)),
    "diamond": ("diamond", "ромб", lambda: [[(50, 18), (81, 50), (50, 82), (19, 50)]]),
    "star": ("star", "звезда", lambda: star(5, 33, 14, 53)),
    "sparkle": ("sparkle", "искра", lambda: star(4, 36, 9)),
    "play": ("play", "плей", lambda: [[(27, 17), (83, 50), (27, 83)]]),
    "lightning": ("lightning", "молния", lambda: [[(60, 6), (25, 57), (47, 57), (37, 94), (77, 39), (55, 39), (68, 6)]]),
    "dash": ("dash", "тире", lambda: [bar((9, 52), (91, 52), W - 1, 10, -8)]),
    "hyphen": ("hyphen", "дефис", lambda: [bar((31, 52), (69, 52), W - 1, -9, 11)]),
    "quote-open": ("quote open", "кавычка открывающая", lambda: chevrons(False)),
    "quote-close": ("quote close", "кавычка закрывающая", lambda: chevrons(True)),
    "quote-low": ("low quote", "нижняя кавычка", lambda: comma(27, 60) + comma(52, 60)),
    "quote-high": ("high quote", "верхняя кавычка", lambda: comma(31, 12, True) + comma(56, 12, True)),
    "exclamation": ("exclamation", "восклицательный знак", lambda: [[(42, 12), (59, 12), (55, 64), (46, 64)]] + blob(50.5, 81, 9.5, 6, 3)),
    "question": ("question", "вопросительный знак",
                 lambda: path([(30, 31), (40, 15), (62, 15), (69, 30), (52, 45), (51, 63)], 13.5, cut0=-30) + blob(51, 81, 9.5, 6, 4)),
    "period": ("period", "точка", lambda: blob(50, 74, 11, 7, 10)),
    "comma": ("comma", "запятая", lambda: comma(42, 60)),
    "colon": ("colon", "двоеточие", lambda: blob(50, 33, 10, 6, 8) + blob(50, 70, 10, 6, 9)),
    "ellipsis": ("ellipsis", "многоточие", lambda: blob(20, 74, 9, 6, 5) + blob(50, 74, 9, 6, 6) + blob(80, 74, 9, 6, 7)),
    "paren-open": ("bracket open", "скобка открывающая", lambda: path([(59, 14), (43, 30), (38, 50), (43, 70), (59, 86)], 13)),
    "paren-close": ("bracket close", "скобка закрывающая", lambda: path([(41, 14), (57, 30), (62, 50), (57, 70), (41, 86)], 13)),
    "plus": ("plus", "плюс", lambda: [bar((18, 50), (82, 50), W, 6, -5), bar((50, 18), (50, 82), W, -4, 7)]),
    "minus": ("minus", "минус", lambda: [bar((18, 51), (82, 51), W, 6, -5)]),
    "equals": ("equals", "равно", lambda: [bar((19, 38), (81, 38), W - 2, 6, -5), bar((19, 63), (81, 63), W - 2, -5, 7)]),
    "check": ("check", "галочка", lambda: path([(16, 50), (40, 74), (85, 25)], W + 1, 10, -10)),
    "cross": ("cross", "крестик", lambda: [bar((20, 20), (80, 80), W), bar((80, 20), (20, 80), W)]),
    "slash": ("slash", "слэш", lambda: [bar((66, 14), (34, 86), W - 1)]),
    "hash": ("hash", "решётка", lambda: [bar((42, 14), (34, 86), W - 3), bar((68, 14), (60, 86), W - 3),
                                         bar((16, 37), (86, 37), W - 3), bar((14, 63), (84, 63), W - 3)]),
    "percent": ("percent", "процент", lambda: [bar((72, 14), (28, 86), W - 3)] + ring(28, 29, 11, 6.5, 7, 11) + ring(72, 71, 11, 6.5, 7, 12)),
    "numero": ("numero", "номер", lambda: [bar((14, 22), (14, 80), 12), bar((14, 22), (46, 80), 12), bar((46, 22), (46, 80), 12)]
                                         + ring(71, 38, 10, 6, 7, 13) + [bar((58, 66), (86, 66), 8)]),
}
STRAIGHT = ("arrow", "dash", "hyphen", "minus", "equals", "plus")  # lean only a little: they line up with the text


def jitter(shapes, seed, amount=.7):
    rnd = random.Random(seed)
    return [[(x + rnd.uniform(-amount, amount), y + rnd.uniform(-amount, amount)) for x, y in s] for s in shapes]


def nudge(mask: Image.Image, dx: float, dy: float) -> Image.Image:
    """Shift without wrapping round the edge (ImageChops.offset would bring what leaves at the bottom back at the top)."""
    out = Image.new("L", mask.size, 0)
    out.paste(mask, (round(dx * K), round(dy * K)))
    return out


def draw_symbol(key: str) -> Image.Image:
    base, colours = VARIANTS.get(key, (key, GREENS))
    seed = sum(map(ord, base))
    rnd = random.Random(seed)
    lean = rnd.uniform(-2, 2) if key.startswith(STRAIGHT) else rnd.uniform(-4, 4)
    shapes = rotate(jitter(SYMBOLS[key][2](), seed), lean)
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    for s in shapes:
        d.polygon([(x * K, y * K) for x, y in s], fill=255)
    rim = grow(m, RIM)
    line = grow(rim, LINE)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    over(img, solid(colours["deep"], nudge(line, 2.4, 2.4)))
    over(img, solid(colours["ink"], line))
    over(img, solid(PAPER, rim))
    over(img, solid(colours["fill"], m))
    stroke, angle = band((0, 30), (100, 62), 22)
    over(img, solid(colours["light"], ImageChops.multiply(brush(stroke, -angle, seed), m)))
    return grain(img.resize((SIZE, SIZE), Image.LANCZOS), 5)
