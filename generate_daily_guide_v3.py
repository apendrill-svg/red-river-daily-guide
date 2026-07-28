#!/usr/bin/env python3
"""
Daily Weather + Tide Guide, v3.
Uses real photos of the Red River Beach lifeguard stand, selected to match
the day's actual conditions, with a chalk-styled vibe panel and a stats
card overlaid as graphic elements (not faked as physically part of the
scene).
"""

import os
import math
import datetime
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from generate_daily_guide import (
    fetch_tides, interpolate_tide, fetch_hourly_weather, pick_hours,
    fetch_buoy, chop_category, DIR_TO_DEG, sky_glyph, sky_icon,
    draw_wind_arrow, lerp_color, draw_centered,
    INK, TEAL, ACCENT, SAND,
)
from generate_daily_guide_v2 import (
    pick_vibe_line, chalk_text, fit_size, rounded_card, F_SCHOOLBELL,
)

FONT_DIR = os.environ.get("DAILY_GUIDE_FONT_DIR", "/usr/share/fonts/truetype/dejavu/")
F_SANS_BOLD = FONT_DIR + "DejaVuSans-Bold.ttf"
F_SANS = FONT_DIR + "DejaVuSans.ttf"
F_MONO = FONT_DIR + "DejaVuSansMono.ttf"
F_MONO_BOLD = FONT_DIR + "DejaVuSansMono-Bold.ttf"

# Harwich's ocean beaches face south into Nantucket Sound (confirmed via
# town/tourism sources). Wind direction from NWS is "blowing FROM" —
# onshore at Red River Beach means wind FROM roughly the south.
BEACH_FACING_DEG = 180
ONSHORE_COLOR = (54, 122, 137)
OFFSHORE_COLOR = (196, 122, 62)
CROSS_COLOR = (128, 128, 122)


_COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def deg_to_compass(deg):
    """Nearest 16-point compass label for a bearing in degrees. The buoy
    reports wind direction in degrees; the WIND line reads 'from the S'."""
    return _COMPASS_16[int((deg % 360) / 22.5 + 0.5) % 16]


def wind_relation(deg):
    d = deg % 360
    if 112.5 <= d < 247.5:
        return "onshore"
    if d >= 292.5 or d < 67.5:
        return "offshore"
    return "cross-shore (E)" if 67.5 <= d < 112.5 else "cross-shore (W)"


def relation_color(relation):
    if relation == "onshore":
        return ONSHORE_COLOR
    if relation == "offshore":
        return OFFSHORE_COLOR
    return CROSS_COLOR


def resolve_current_wind(buoy, fc_deg, fc_wdir, fc_kt):
    """Pick the 'right now' wind for the headline anchor line: the live buoy
    reading (sustained speed, gust, direction) when available, otherwise the
    NWS forecast. The hourly planning arrows stay forecast-based separately."""
    if buoy and buoy.get("wspd_kt") is not None and buoy.get("wdir_deg") is not None:
        return {
            "kt": buoy["wspd_kt"],
            "deg": buoy["wdir_deg"],
            "wdir": deg_to_compass(buoy["wdir_deg"]),
            "gust": buoy.get("gust_kt"),
            "source": "buoy",
        }
    return {"kt": fc_kt, "deg": fc_deg, "wdir": fc_wdir, "gust": None, "source": "forecast"}


def wind_readout(cur):
    """Structured, legible wind pieces for the headline: a big speed hero,
    an optional gust callout, and a secondary 'what it means' line. Splitting
    these lets the render size the speed large instead of cramming six facts
    onto one auto-shrunk line."""
    relation = wind_relation(cur["deg"])
    rel_label = "CROSS-SHORE" if relation.startswith("cross") else relation.upper()
    return {
        "speed": f"{cur['kt']:.0f} kt",
        "gust": None if cur["gust"] is None else f"gusts {cur['gust']:.0f}",
        "desc": f"{rel_label} · from the {cur['wdir']} · {wind_speed_band(cur['kt'])}",
        "relation": relation,
    }


def wind_speed_band(kt):
    if kt < 5:
        return "calm"
    if kt < 8:
        return "light"
    if kt < 13:
        return "gentle"
    if kt < 19:
        return "moderate"
    if kt < 25:
        return "fresh"
    return "strong"


def kite_verdict(kt, relation):
    """Plain-language, honest verdict — not just a speed threshold."""
    if kt < 5:
        return "too light to fly", False
    if kt > 26:
        return "too strong — small-craft caution territory", False
    if relation == "offshore":
        return "flyable, but offshore — gustier, and gear can drift out over water", None
    if 8 <= kt <= 20:
        return "ideal", True
    if kt < 8:
        return "light — small or kids' kites only", None
    return "strong — experienced fliers", None


def draw_shore_wind_diagram(draw, x0, y0, w, h, deg, kt, relation):
    """[Retained for API compatibility, no longer used in the boxless
    layout.] Small shoreline cross-section with a wind arrow. Replaced
    by draw_wind_arrow_glyph, which keeps only the arrow (no opaque
    sand/water strip) to match the transparent-overlay aesthetic."""
    return


def draw_wind_arrow_glyph(draw, cx, cy, radius, deg, color, shadow_color=(0, 0, 0, 200)):
    """A single wind arrow centered on (cx, cy). Points in the direction
    the wind is BLOWING TOWARD, which is opposite of `deg` (NWS reports
    'from' direction). Line + triangle head, colored by shore-relation.
    A soft outline sits behind it for legibility on any photo tone."""
    travel_deg = (deg + 180) % 360
    rad = math.radians(travel_deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    tip = (cx + dx * radius, cy + dy * radius)
    tail = (cx - dx * radius, cy - dy * radius)
    shaft_w = max(int(radius * 0.14), 3)
    perp = (-dy, dx)
    head_len = radius * 0.42
    head_w = radius * 0.28
    left = (tip[0] - dx * head_len + perp[0] * head_w, tip[1] - dy * head_len + perp[1] * head_w)
    right = (tip[0] - dx * head_len - perp[0] * head_w, tip[1] - dy * head_len - perp[1] * head_w)

    outline_w = shaft_w + 4
    draw.line([tail, tip], fill=shadow_color, width=outline_w)
    draw.polygon([tip, left, right], fill=shadow_color, outline=shadow_color)
    draw.line([tail, tip], fill=color, width=shaft_w)
    draw.polygon([tip, left, right], fill=color, outline=color)


def add_bottom_scrim(img, top_frac=0.68, max_alpha=115):
    """Composite a soft dark gradient over the bottom portion of img so
    overlay text remains readable against busy photo content (sand, tire
    tracks, foliage). Gradient is fully transparent at `top_frac` of the
    canvas height and eases down to `max_alpha` at the bottom. Non-linear
    so the transition is imperceptible at the top edge."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    scrim_top = int(h * top_frac)
    scrim_h = h - scrim_top
    if scrim_h <= 0:
        return img
    gradient = Image.new("L", (1, scrim_h))
    for py in range(scrim_h):
        f = py / max(scrim_h - 1, 1)
        gradient.putpixel((0, py), int(max_alpha * (f ** 1.6)))
    gradient = gradient.resize((w, scrim_h))
    scrim = Image.new("RGBA", (w, scrim_h), (0, 0, 0))
    scrim.putalpha(gradient)
    img.alpha_composite(scrim, (0, scrim_top))
    return img

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(SCRIPT_DIR, "photos") + os.sep
PHOTO_LIBRARY = {
    "clear": [PHOTO_DIR + "bright.jpeg"],
    "cloudy": [PHOTO_DIR + "overcast.jpeg"],
    "rain": [PHOTO_DIR + "overcast.jpeg"],
    "dusk": [PHOTO_DIR + "dusk_sign.jpeg", PHOTO_DIR + "dusk_small.jpeg"],
}

# Stand position varies per photo, so panel/card placement is tuned per
# photo rather than assumed. panel_w_frac / card_w_frac are fractions of
# canvas width; sides are measured clear of the stand's actual footprint.
PLACEMENTS = {
    "bright.jpeg":     {"panel_w_frac": 0.24, "card_w_frac": 1.0},
    "overcast.jpeg":   {"panel_w_frac": 0.28, "card_w_frac": 1.0},
    "dusk_sign.jpeg":  {"panel_w_frac": 0.30, "card_w_frac": 1.0},
    "dusk_small.jpeg": {"panel_w_frac": 0.38, "card_w_frac": 1.0},
}
DEFAULT_PLACEMENT = {"panel_w_frac": 0.26, "card_w_frac": 1.0}

TARGET_W = 1400  # upscale target; source photos are modest resolution


def select_photo(category, date):
    pool = PHOTO_LIBRARY.get(category, PHOTO_LIBRARY["clear"])
    rnd = random.Random(date.strftime("%Y%m%d") + category)
    return rnd.choice(pool)


def load_scaled(path, target_w=TARGET_W):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = target_w / w
    return img.resize((target_w, int(h * scale)), Image.LANCZOS)


def measure_width_font(text, font_path, size):
    fnt = ImageFont.truetype(font_path, size)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    b = d.textbbox((0, 0), text, font=fnt)
    return b[2] - b[0]


def fit_size_font(text, font_path, start_size, max_width, min_size=12):
    size = start_size
    while size > min_size and measure_width_font(text, font_path, size) > max_width:
        size -= 1
    return size


def _soft_text(draw, xy, text, font, fill, stroke=2):
    """Text with a subtle dark stroke so it stays legible over any photo,
    without a card behind it. `fill` may be a 3- or 4-tuple; the stroke
    uses ~80% black."""
    draw.text(xy, text, font=font, fill=fill,
              stroke_width=stroke, stroke_fill=(0, 0, 0, 200))


def _soft_center(draw, cx, y, text, font, fill, stroke=2):
    """Draw `text` horizontally centered on `cx` at row `y`, with soft stroke."""
    w = draw.textbbox((0, 0), text, font=font)[2]
    _soft_text(draw, (cx - w // 2, y), text, font, fill, stroke)


def _row_centered(draw, cx, y, tokens, gap=0):
    """Lay out a mixed-color/font row centered on cx. Tokens: [(text, font, color), ...]."""
    widths = [draw.textbbox((0, 0), t, font=f)[2] for t, f, _ in tokens]
    total = sum(widths) + gap * (len(tokens) - 1)
    x = cx - total // 2
    for (text, font, color), w in zip(tokens, widths):
        _soft_text(draw, (x, y), text, font, color)
        x += w + gap


def build_stats_card(location_name, sub, date, tides_today, tide_window,
                      tide_8am, tide_5pm, hours, buoy, cw=460, ch=None, scale=2.0):
    # Full-canvas boxless layout: card fills the entire page, everything
    # centered horizontally, text drawn directly on the photo with a soft
    # dark stroke for legibility.
    S = scale
    def sz(n): return max(int(round(n * S)), 6)
    def sp(n): return int(round(n * S))
    if ch is None:
        ch = sp(800)
    shadow = Image.new("RGBA", (cw + 50, ch + 50), (0, 0, 0, 0))  # no-op, kept for return shape

    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    CX = cw // 2
    pad = sp(80)  # generous horizontal margins for lines/diagram, text is centered on CX
    y = sp(60)     # top breathing room
    HEAD = (144, 220, 230, 255)
    BODY = (255, 255, 255, 255)
    MUTED = (230, 226, 214, 220)
    HIGH = (255, 200, 90, 255)
    RULE = (255, 255, 255, 90)

    _soft_center(draw, CX, y, location_name.upper(), ImageFont.truetype(F_MONO_BOLD, sz(22)), HEAD)
    y += sp(32)
    _soft_center(draw, CX, y, sub, ImageFont.truetype(F_MONO, sz(15)), MUTED)
    y += sp(26)
    _soft_center(draw, CX, y, date.strftime("%A, %B %-d"), ImageFont.truetype(F_MONO, sz(17)), BODY)
    y += sp(52)

    _soft_center(draw, CX, y, "TIDE TODAY", ImageFont.truetype(F_MONO_BOLD, sz(18)), HEAD)
    y += sp(40)
    row_h = sp(46)
    f_label = ImageFont.truetype(F_SANS_BOLD, sz(23))
    f_time = ImageFont.truetype(F_MONO, sz(22))
    for e in tides_today:
        is_high = e["type"] == "H"
        arrow = "▲" if is_high else "▼"
        label = "High" if is_high else "Low"
        col = HIGH if is_high else HEAD
        hgt_txt = f"{e['height']:.1f} ft"
        tokens = [
            (arrow, f_label, col),
            (f"  {label}", f_label, BODY),
            (f"    {e['time'].strftime('%-I:%M %p')}", f_time, BODY),
            (f"    {hgt_txt}", f_time, BODY),
        ]
        _row_centered(draw, CX, y, tokens)
        y += row_h
    y += sp(12)

    tline = ""
    if tide_8am:
        tline += f"8am {tide_8am['height']:.1f}ft{'↑' if tide_8am['rising'] else '↓'}   "
    if tide_5pm:
        tline += f"5pm {tide_5pm['height']:.1f}ft{'↑' if tide_5pm['rising'] else '↓'}"
    _soft_center(draw, CX, y, tline, ImageFont.truetype(F_MONO, sz(17)), MUTED)
    y += sp(44)
    draw.line([(pad, y), (cw - pad, y)], fill=RULE, width=2)
    y += sp(30)

    # WIND — plain-language onshore/offshore read for actually sitting on
    # the beach. The headline "right now" line prefers the live buoy reading
    # (sustained speed, gust, and direction) over the NWS forecast, so it
    # matches what's actually felt on the sand; it falls back to the midday
    # forecast period only when the buoy wind is unavailable. The hourly
    # arrows below stay forecast-based for planning ahead.
    _soft_center(draw, CX, y, "WIND", ImageFont.truetype(F_MONO_BOLD, sz(18)), HEAD)
    y += sp(40)
    midday = next((h for h in hours if h[0] == 12), hours[len(hours) // 2])
    mid_period = midday[1]
    fc_wdir = mid_period["windDirection"]
    fc_deg = DIR_TO_DEG.get(fc_wdir, 0)
    fc_kt = float(mid_period["windSpeed"].split()[0])

    cur = resolve_current_wind(buoy, fc_deg, fc_wdir, fc_kt)
    relation = wind_relation(cur["deg"])
    rcol = relation_color(relation)
    readout = wind_readout(cur)

    # Big rotated arrow replaces the old opaque sand/water strip. Points
    # in the direction the wind is BLOWING TOWARD; colored by shore
    # relation (teal onshore, warm offshore, gray cross-shore).
    arrow_r = sp(48)
    arrow_cy = y + arrow_r
    draw_wind_arrow_glyph(draw, CX, arrow_cy, arrow_r, cur["deg"], rcol)
    y = arrow_cy + arrow_r + sp(24)

    # Speed hero, centered. Gust (when present) sits inline just after it.
    f_speed = ImageFont.truetype(F_MONO_BOLD, sz(32))
    if readout["gust"]:
        f_gust = ImageFont.truetype(F_MONO_BOLD, sz(20))
        _row_centered(draw, CX, y, [
            (readout["speed"], f_speed, BODY),
            (f"   {readout['gust']}", f_gust, HIGH),
        ])
    else:
        _soft_center(draw, CX, y, readout["speed"], f_speed, BODY)
    y += sp(50)
    desc_sz = fit_size_font(readout["desc"], F_SANS_BOLD, sz(18), cw - 2 * pad, min_size=sz(14))
    _soft_center(draw, CX, y, readout["desc"], ImageFont.truetype(F_SANS_BOLD, desc_sz), BODY)
    y += sp(38)
    verdict_text, is_good = kite_verdict(cur["kt"], relation)
    verdict_col = HIGH if is_good else MUTED
    verdict_line = f"Kite flying: {verdict_text}"
    verdict_sz = fit_size_font(verdict_line, F_SANS, sz(17), cw - 2 * pad, min_size=sz(12))
    _soft_center(draw, CX, y, verdict_line, ImageFont.truetype(F_SANS, verdict_sz), verdict_col)
    y += sp(44)
    draw.line([(pad, y), (cw - pad, y)], fill=RULE, width=2)
    y += sp(30)

    subset = hours[:4]
    col_w = (cw - 2 * pad) / len(subset)
    icon_y = y + sp(36)
    for i, (th, period, t) in enumerate(subset):
        cx = pad + col_w * i + col_w / 2
        _soft_center(draw, cx, y, t.strftime("%-I%p").lower(), ImageFont.truetype(F_MONO, sz(15)), BODY)
        sky_icon(draw, cx, icon_y, sp(16), sky_glyph(period["shortForecast"]))
        draw_centered(draw, cx, icon_y + sp(22), f"{period['temperature']}°", ImageFont.truetype(F_SANS_BOLD, sz(19)), BODY)
        wdir = period["windDirection"]
        deg = DIR_TO_DEG.get(wdir, 0)
        draw_wind_arrow(draw, cx, icon_y + sp(56), deg, sp(12), relation_color(wind_relation(deg)))
    y = icon_y + sp(90)

    draw.line([(pad, y), (cw - pad, y)], fill=RULE, width=2)
    y += sp(26)
    cat, _ = chop_category(buoy["wave_ft"] if buoy else None)
    if buoy:
        chop_line = f"CHOP  {cat} · {buoy['wave_ft']}ft"
        if buoy["wspd_kt"]:
            chop_line += f"  ·  wind {buoy['wspd_kt']:.0f}kt"
    else:
        chop_line = "CHOP  unavailable"
    chop_sz = fit_size_font(chop_line, F_MONO_BOLD, sz(18), cw - 2 * pad, min_size=sz(13))
    _soft_center(draw, CX, y, chop_line, ImageFont.truetype(F_MONO_BOLD, chop_sz), BODY)
    y += sp(36)
    tsw = datetime.datetime.now().strftime("%-I:%M%p")
    _soft_center(draw, CX, y, f"NOAA · NWS · NDBC 44020  ·  upd {tsw}",
                 ImageFont.truetype(F_MONO, sz(11)), MUTED, stroke=1)
    return shadow, card


def build_chalk_panel(vibe, date, panel_w):
    """Chalk-textured graphic panel, treated as an overlay label, not an
    object faked into the scene."""
    inner_w = panel_w - 64
    lines = vibe.split("\n")
    rendered = []
    for i, l in enumerate(lines):
        target = 56 if i == 0 else 46
        sz = fit_size(l, target, inner_w)
        rendered.append(chalk_text(l, sz, rotation=random.uniform(-1.2, 1.2)))
    date_tag = chalk_text(date.strftime("%a %-m/%-d"), 20, grain=0.28, rotation=-2)

    line_gap = 12
    content_h = sum(r.size[1] for r in rendered) - sum(int(r.size[1] * 0.30) for r in rendered[1:]) + line_gap
    panel_h = int(content_h + date_tag.size[1] * 0.6 + 70)

    panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle([0, 0, panel_w, panel_h], radius=18, fill=(30, 48, 40, 235))
    pd.rounded_rectangle([6, 6, panel_w - 6, panel_h - 6], radius=14, outline=(90, 70, 50, 255), width=4)

    cy = 30
    for i, layer in enumerate(rendered):
        panel.alpha_composite(layer, (int(panel_w / 2 - layer.size[0] / 2), cy))
        cy += int(layer.size[1] * 0.72)
    panel.alpha_composite(date_tag, (panel_w - date_tag.size[0] - 18, panel_h - date_tag.size[1] - 10))

    shadow = Image.new("RGBA", (panel_w + 40, panel_h + 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([20, 24, 20 + panel_w, 24 + panel_h], radius=18, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    return shadow, panel


def extend_canvas_for_phone(img, target_ratio=0.69):
    """Extends the canvas downward with more sand so the image fills a
    phone viewport when fit to width, instead of leaving a letterboxed
    gap. Samples the photo's own bottom strip and tiles it down, with a
    soft blend at the seam so it doesn't read as an obvious graft."""
    w, h = img.size
    target_h = int(w / target_ratio)
    if target_h <= h:
        return img
    extra = target_h - h
    strip_h = 60
    strip = img.crop((0, h - strip_h, w, h))
    new_img = Image.new("RGBA", (w, target_h), (0, 0, 0, 0))
    new_img.paste(img, (0, 0))
    y = h
    while y < target_h:
        new_img.paste(strip, (0, y))
        y += strip_h
    # soften the seam and any tiling repetition
    band = new_img.crop((0, h - 20, w, min(h + 140, target_h)))
    band = band.filter(ImageFilter.GaussianBlur(6))
    new_img.paste(band, (0, h - 20))
    return new_img


def main():
    now = datetime.datetime.now()
    yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")
    tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y%m%d")

    tide_window = fetch_tides(yesterday_str, tomorrow_str)
    tides_today = [e for e in tide_window if e["time"].date() == now.date()]
    tide_8am = interpolate_tide(tide_window, now.replace(hour=8, minute=0, second=0, microsecond=0))
    tide_5pm = interpolate_tide(tide_window, now.replace(hour=17, minute=0, second=0, microsecond=0))
    periods = fetch_hourly_weather()
    hours = pick_hours(periods)
    buoy = fetch_buoy()

    vibe, category, trend = pick_vibe_line(hours, now)
    photo_path = select_photo(category, now)
    print(f"category={category} trend={trend} vibe={vibe!r} photo={photo_path}")

    img = load_scaled(photo_path).convert("RGBA")
    img = extend_canvas_for_phone(img, target_ratio=0.69)
    img = add_bottom_scrim(img)
    W, H = img.size
    print("canvas", W, H)

    # Full-canvas boxless overlay: info fills the entire page, centered.
    # The old top-left location tag and the bottom-left chalk vibe panel are
    # intentionally skipped in this layout — the centered card carries the
    # location line itself, and the chalk panel would collide with centered
    # content.
    placement = PLACEMENTS.get(os.path.basename(photo_path), DEFAULT_PLACEMENT)
    shadow, card = build_stats_card("Red River Beach", "Harwichport, MA", now,
                                      tides_today, tide_window, tide_8am, tide_5pm, hours, buoy,
                                      cw=W, ch=H)
    img.alpha_composite(card, (0, 0))

    out = img.convert("RGB")
    path = f"daily_guide_v3_{now.strftime('%Y%m%d')}.png"
    out.save(path, quality=92)
    print("saved", path)


if __name__ == "__main__":
    main()
