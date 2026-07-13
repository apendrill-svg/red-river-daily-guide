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
        return "good kite flying wind", True
    if kt < 8:
        return "light — small or kids' kites only", None
    return "strong — experienced fliers", None


def draw_shore_wind_diagram(draw, x0, y0, w, h, deg, kt, relation):
    """Small shoreline cross-section with a wind arrow, so 'onshore vs
    offshore' is something you see, not something you have to decode from
    a compass letter."""
    shore_y = y0 + h * 0.42
    draw.rectangle([x0, y0, x0 + w, shore_y], fill=(230, 217, 184))
    draw.rectangle([x0, shore_y, x0 + w, y0 + h], fill=(68, 118, 122))
    draw.line([(x0, shore_y), (x0 + w, shore_y)], fill=(110, 88, 60), width=3)
    f_tiny = ImageFont.truetype(F_MONO, 11)
    draw.text((x0 + 10, y0 + 6), "SAND", font=f_tiny, fill=(130, 110, 78))
    draw.text((x0 + 10, y0 + h - 20), "WATER", font=f_tiny, fill=(215, 228, 226))

    cx, cy = x0 + w * 0.68, y0 + h * 0.5
    travel_deg = (deg + 180) % 360
    rad = math.radians(travel_deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    L = h * 0.36
    tip = (cx + dx * L, cy + dy * L)
    tail = (cx - dx * L, cy - dy * L)
    col = relation_color(relation)
    draw.line([tail, tip], fill=col, width=5)
    perp = (-dy, dx)
    left = (tip[0] - dx * 11 + perp[0] * 7, tip[1] - dy * 11 + perp[1] * 7)
    right = (tip[0] - dx * 11 - perp[0] * 7, tip[1] - dy * 11 - perp[1] * 7)
    draw.polygon([tip, left, right], fill=col)

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
    "bright.jpeg":     {"panel_w_frac": 0.24, "card_w_frac": 0.33},
    "overcast.jpeg":   {"panel_w_frac": 0.30, "card_w_frac": 0.30},
    "dusk_sign.jpeg":  {"panel_w_frac": 0.34, "card_w_frac": 0.30},
    "dusk_small.jpeg": {"panel_w_frac": 0.40, "card_w_frac": 0.40},
}
DEFAULT_PLACEMENT = {"panel_w_frac": 0.28, "card_w_frac": 0.33}

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


def build_stats_card(location_name, sub, date, tides_today, tide_window,
                      tide_8am, tide_5pm, hours, buoy, cw=460):
    ch = 690
    shadow = Image.new("RGBA", (cw + 50, ch + 50), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([22, 26, 22 + cw, 26 + ch], radius=26, fill=(0, 0, 0, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))

    card = rounded_card((cw, ch), radius=26, fill=(251, 248, 242, 240))
    draw = ImageDraw.Draw(card)
    pad = 26
    y = pad
    draw.text((pad, y), location_name.upper(), font=ImageFont.truetype(F_MONO_BOLD, 16), fill=TEAL)
    y += 22
    draw.text((pad, y), date.strftime("%A, %B %-d"), font=ImageFont.truetype(F_MONO, 14), fill=INK)
    y += 30

    y += 6
    draw.text((pad, y), "TIDE TODAY", font=ImageFont.truetype(F_MONO_BOLD, 15), fill=TEAL)
    y += 30
    row_h = 34
    f_label = ImageFont.truetype(F_SANS_BOLD, 19)
    f_time = ImageFont.truetype(F_MONO, 18)
    for e in tides_today:
        is_high = e["type"] == "H"
        arrow = "▲" if is_high else "▼"
        label = "High" if is_high else "Low"
        col = ACCENT if is_high else TEAL
        draw.text((pad, y), arrow, font=f_label, fill=col)
        draw.text((pad + 30, y), label, font=f_label, fill=INK)
        draw.text((pad + 110, y + 1), e["time"].strftime("%-I:%M %p"), font=f_time, fill=INK)
        hgt_txt = f"{e['height']:.1f} ft"
        hw = draw.textbbox((0, 0), hgt_txt, font=f_time)[2]
        draw.text((cw - pad - hw, y + 1), hgt_txt, font=f_time, fill=INK)
        y += row_h
    y += 6

    tline = ""
    if tide_8am:
        tline += f"8am {tide_8am['height']:.1f}ft{'↑' if tide_8am['rising'] else '↓'}   "
    if tide_5pm:
        tline += f"5pm {tide_5pm['height']:.1f}ft{'↑' if tide_5pm['rising'] else '↓'}"
    draw.text((pad, y), tline, font=ImageFont.truetype(F_MONO, 14), fill=(120, 120, 110))
    y += 32
    draw.line([(pad, y), (cw - pad, y)], fill=SAND, width=2)
    y += 18

    # WIND — plain-language onshore/offshore read for actually sitting on
    # the beach, anchored to midday (or nearest available hour)
    draw.text((pad, y), "WIND", font=ImageFont.truetype(F_MONO_BOLD, 15), fill=TEAL)
    y += 26
    midday = next((h for h in hours if h[0] == 12), hours[len(hours) // 2])
    mid_period = midday[1]
    mid_wdir = mid_period["windDirection"]
    mid_deg = DIR_TO_DEG.get(mid_wdir, 0)
    mid_kt = float(mid_period["windSpeed"].split()[0])
    relation = wind_relation(mid_deg)

    diagram_h = 78
    draw_shore_wind_diagram(draw, pad, y, cw - 2 * pad, diagram_h, mid_deg, mid_kt, relation)
    y += diagram_h + 12

    band = wind_speed_band(mid_kt)
    rel_label = relation.replace("-shore", "").upper()
    draw.text((pad, y), f"{rel_label} · {mid_kt:.0f}kt {band} · from the {mid_wdir}",
              font=ImageFont.truetype(F_MONO_BOLD, 15), fill=relation_color(relation))
    y += 24
    verdict_text, is_good = kite_verdict(mid_kt, relation)
    verdict_col = ACCENT if is_good else (100, 100, 92)
    draw.text((pad, y), f"Kite flying: {verdict_text}",
              font=ImageFont.truetype(F_SANS, 14), fill=verdict_col)
    y += 30
    draw.line([(pad, y), (cw - pad, y)], fill=SAND, width=2)
    y += 16

    subset = hours[::max(len(hours) // 4, 1)][:4]
    col_w = (cw - 2 * pad) / len(subset)
    icon_y = y + 32
    for i, (th, period, t) in enumerate(subset):
        cx = pad + col_w * i + col_w / 2
        draw.text((cx - 14, y), t.strftime("%-I%p").lower(), font=ImageFont.truetype(F_MONO, 13), fill=INK)
        sky_icon(draw, cx, icon_y, 13, sky_glyph(period["shortForecast"]))
        draw_centered(draw, cx, icon_y + 18, f"{period['temperature']}°", ImageFont.truetype(F_SANS_BOLD, 16), INK)
        wdir = period["windDirection"]
        deg = DIR_TO_DEG.get(wdir, 0)
        draw_wind_arrow(draw, cx, icon_y + 48, deg, 10, relation_color(wind_relation(deg)))
    y = icon_y + 64

    draw.line([(pad, y), (cw - pad, y)], fill=SAND, width=2)
    y += 14
    cat, _ = chop_category(buoy["wave_ft"] if buoy else None)
    if buoy:
        chop_line = f"CHOP  {cat} · {buoy['wave_ft']}ft"
        if buoy["wspd_kt"]:
            chop_line += f"  ·  wind {buoy['wspd_kt']:.0f}kt"
    else:
        chop_line = "CHOP  unavailable"
    draw.text((pad, y), chop_line, font=ImageFont.truetype(F_MONO_BOLD, 15), fill=INK)
    y += 24
    tsw = datetime.datetime.now().strftime("%-I:%M%p")
    draw.text((pad, y), f"NOAA · NWS · NDBC 44020  ·  upd {tsw}",
              font=ImageFont.truetype(F_MONO, 11), fill=(140, 140, 130))
    return shadow, card


def build_chalk_panel(vibe, date, panel_w):
    """Chalk-textured graphic panel, treated as an overlay label, not an
    object faked into the scene."""
    inner_w = panel_w - 64
    lines = vibe.split("\n")
    rendered = []
    for i, l in enumerate(lines):
        target = 46 if i == 0 else 38
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
    W, H = img.size
    print("canvas", W, H)

    # location tag, top-left
    draw = ImageDraw.Draw(img)
    tag = "Red River Beach · Harwichport, MA"
    for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        draw.text((40 + ox, 34 + oy), tag, font=ImageFont.truetype(F_SANS_BOLD, 26), fill=(0, 0, 0))
    draw.text((40, 34), tag, font=ImageFont.truetype(F_SANS_BOLD, 26), fill=(255, 255, 255))

    # chalk panel, bottom-left, width tuned per photo to clear the stand
    placement = PLACEMENTS.get(os.path.basename(photo_path), DEFAULT_PLACEMENT)
    panel_w = int(W * placement["panel_w_frac"])
    pshadow, panel = build_chalk_panel(vibe, now, panel_w)
    px = 40
    py = H - panel.size[1] - 50
    img.alpha_composite(pshadow, (px - 20, py - 24))
    img.alpha_composite(panel, (px, py))

    # stats card, bottom-right, width tuned per photo
    card_w = max(int(W * placement["card_w_frac"]), 380)
    shadow, card = build_stats_card("Red River Beach", "Harwichport, MA", now,
                                      tides_today, tide_window, tide_8am, tide_5pm, hours, buoy,
                                      cw=card_w)
    cx = W - card.size[0] - 40
    cy = H - card.size[1] - 50
    img.alpha_composite(shadow, (cx - 22, cy - 26))
    img.alpha_composite(card, (cx, cy))

    out = img.convert("RGB")
    path = f"daily_guide_v3_{now.strftime('%Y%m%d')}.png"
    out.save(path, quality=92)
    print("saved", path)


if __name__ == "__main__":
    main()
