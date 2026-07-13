#!/usr/bin/env python3
"""
Daily Weather + Tide Guide, v2.
Full beach scene (placeholder until real photo is supplied) with a
lifeguard-stand chalkboard carrying a short, condition-aware vibe line,
and a separate compact stats card overlaid on the photo.
"""

import random
import datetime
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from generate_daily_guide import (
    LOCATIONS, fetch_tides, interpolate_tide, fetch_hourly_weather,
    pick_hours, fetch_buoy, chop_category, DIR_TO_DEG, sky_glyph, sky_icon,
    draw_wind_arrow, draw_vgradient, lerp_color, text_w, draw_centered,
    INK, TEAL, ACCENT, CREAM, SAND, SKY_TOP, SKY_BOT, WATER, WATER_DK,
)

import os
F_SCHOOLBELL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Schoolbell.ttf")
FONT_DIR = os.environ.get("DAILY_GUIDE_FONT_DIR", "/usr/share/fonts/truetype/dejavu/")
F_SERIF_BOLD = FONT_DIR + "DejaVuSerif-Bold.ttf"
F_SANS = FONT_DIR + "DejaVuSans.ttf"
F_SANS_BOLD = FONT_DIR + "DejaVuSans-Bold.ttf"
F_MONO = FONT_DIR + "DejaVuSansMono.ttf"
F_MONO_BOLD = FONT_DIR + "DejaVuSansMono-Bold.ttf"

W, H = 1080, 1620

VIBE_BANK = {
    ("clear", "improving"): [
        "typical Cape day\ngetting sunnier later",
        "burning off quick\nblue skies by noon",
    ],
    ("clear", "steady"): [
        "it's beautiful here\ncome on down",
        "sun's out all day\nsand's warm, water's fine",
    ],
    ("clear", "clouding"): [
        "gorgeous this morning\nclouds roll in later",
        "get here early\nbest light before 3",
    ],
    ("cloudy", "improving"): [
        "overcast now\nclearing up this afternoon",
        "gray start, good finish\nsun's coming",
    ],
    ("cloudy", "steady"): [
        "soft light today\nstill a good beach day",
        "no burn today\nbring a book",
    ],
    ("cloudy", "clouding"): [
        "keep an eye on the sky\nstill walkable all day",
        "quiet gray day\nlow tide's the move",
    ],
    ("rain", "improving"): [
        "wet start\nshaping up by afternoon",
        "rain's clearing out\nhang tight",
    ],
    ("rain", "steady"): [
        "rain today\nchowder weather",
        "not a beach day\nsee you tomorrow",
    ],
    ("rain", "clouding"): [
        "damp and staying that way\nrain gear if you're coming",
        "soggy one\nbeach walk still counts",
    ],
}

WEEKEND_EXTRA = {
    5: ["Saturday\nsand between your toes"],   # Saturday
    6: ["Sunday Funday\nlet's go"],             # Sunday
}


def sky_score(short_forecast):
    s = short_forecast.lower()
    if "sunny" in s or "clear" in s:
        return 3.0
    if "mostly sunny" in s or "partly" in s or "few clouds" in s:
        return 2.2
    if "cloud" in s or "overcast" in s:
        return 1.0
    if "fog" in s:
        return 0.6
    if "rain" in s or "shower" in s or "storm" in s or "thunder" in s:
        return 0.0
    return 1.5


def pick_vibe_line(hours, date):
    scores = [sky_score(p["shortForecast"]) for _, p, _ in hours]
    half = len(scores) // 2
    early_avg = sum(scores[:half]) / max(half, 1)
    late_avg = sum(scores[half:]) / max(len(scores) - half, 1)
    has_rain = any(s == 0.0 for s in scores)

    if has_rain:
        category = "rain"
    elif sum(scores) / len(scores) < 1.5:
        category = "cloudy"
    else:
        category = "clear"

    diff = late_avg - early_avg
    if diff > 0.4:
        trend = "improving"
    elif diff < -0.4:
        trend = "clouding"
    else:
        trend = "steady"

    rnd = random.Random(date.strftime("%Y%m%d"))
    pool = list(VIBE_BANK[(category, trend)])
    weekday = date.weekday()
    if weekday in WEEKEND_EXTRA and rnd.random() < 0.5:
        pool = pool + WEEKEND_EXTRA[weekday]
    return rnd.choice(pool), category, trend


# ---------------------------------------------------------------------
# CHALK RENDERING
# ---------------------------------------------------------------------

_rng = np.random.default_rng()

def chalk_text(text, size, color=(238, 236, 225), rotation=0.0, grain=0.32, blur=0.55):
    fnt = ImageFont.truetype(F_SCHOOLBELL, size)
    tmp = Image.new("RGBA", (10, 10))
    tdraw = ImageDraw.Draw(tmp)
    lines = text.split("\n")
    line_h = size * 1.25
    widths = [tdraw.textbbox((0, 0), l, font=fnt)[2] for l in lines]
    tw, th = max(widths), int(line_h * len(lines))
    pad = int(size * 0.5)
    layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    for i, l in enumerate(lines):
        ldraw.text((pad, pad + i * line_h), l, font=fnt, fill=color + (255,))
    arr = np.array(layer)
    alpha = arr[:, :, 3].astype(np.float32)
    noise = _rng.random(alpha.shape)
    alpha = alpha * np.clip(noise + (1 - grain), 0, 1)
    arr[:, :, 3] = alpha.astype(np.uint8)
    layer = Image.fromarray(arr, "RGBA")
    if blur > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    if rotation:
        layer = layer.rotate(rotation, resample=Image.BICUBIC, expand=True)
    return layer


def measure_width(text, size):
    fnt = ImageFont.truetype(F_SCHOOLBELL, size)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    b = d.textbbox((0, 0), text, font=fnt)
    return b[2] - b[0]


def fit_size(text, start_size, max_width, min_size=24):
    size = start_size
    while size > min_size and measure_width(text, size) > max_width:
        size -= 2
    return size


def paste(base, layer, xy, anchor="la"):
    x, y = xy
    lw, lh = layer.size
    if "m" in anchor[0:1] or anchor[0] == "m":
        x -= lw / 2
    if anchor[0] == "r":
        x -= lw
    if len(anchor) > 1 and anchor[1] == "m":
        y -= lh / 2
    base.paste(layer, (int(x), int(y)), layer)


# ---------------------------------------------------------------------
# SCENE
# ---------------------------------------------------------------------

def full_beach_scene(size):
    """Placeholder full-bleed beach scene with a lifeguard stand + chalk
    sign. Swap for the real photo once supplied; the chalk + card
    compositing logic stays the same."""
    w, h = size
    img = Image.new("RGB", size, SKY_TOP)
    draw = ImageDraw.Draw(img)
    horizon = int(h * 0.46)
    waterline = int(h * 0.60)
    draw_vgradient(draw, (0, 0, w, horizon), (150, 198, 222), (224, 220, 196))
    draw_vgradient(draw, (0, horizon, w, waterline), WATER, WATER_DK)
    draw_vgradient(draw, (0, waterline, w, h), (222, 202, 156), (196, 174, 128))
    draw.ellipse([w - 260, 70, w - 120, 210], fill=(247, 214, 150))

    # gentle wave lines
    for i in range(6):
        y = horizon + 20 + i * 22
        draw.line([(0, y), (w, y + (10 if i % 2 else -6))],
                   fill=(255, 255, 255, 40), width=2)

    # lifeguard stand (simple chair-on-tower silhouette)
    stand_x = int(w * 0.66)
    base_y = waterline + 40
    leg_top = base_y - 210
    for dx in (-70, 70):
        draw.line([(stand_x + dx, base_y), (stand_x + dx * 0.25, leg_top)],
                   fill=(120, 90, 60), width=10)
    draw.line([(stand_x - 70, base_y - 90), (stand_x + 70, base_y - 90)],
               fill=(120, 90, 60), width=8)
    draw.rectangle([stand_x - 55, leg_top - 70, stand_x + 55, leg_top],
                    fill=(235, 240, 238), outline=(120, 90, 60), width=6)
    draw.rectangle([stand_x - 40, leg_top - 130, stand_x + 40, leg_top - 68],
                    fill=(210, 60, 55))

    # chalkboard sign on its own post, foreground left of the stand
    board_w, board_h = 420, 300
    board_x = int(w * 0.12)
    board_y = int(h * 0.66)
    post_y0 = board_y + board_h - 20
    for dx in (30, board_w - 30):
        draw.rectangle([board_x + dx - 8, post_y0, board_x + dx + 8, post_y0 + 120],
                        fill=(120, 90, 60))
    frame_pad = 14
    draw.rectangle([board_x - frame_pad, board_y - frame_pad,
                     board_x + board_w + frame_pad, board_y + board_h + frame_pad],
                    fill=(96, 68, 46))
    draw.rectangle([board_x, board_y, board_x + board_w, board_y + board_h],
                    fill=(38, 60, 50))

    # sand texture speckle
    for _ in range(400):
        x = _rng.integers(0, w)
        y = _rng.integers(waterline, h)
        c = 210 + _rng.integers(-15, 15)
        draw.point((x, y), fill=(c, c - 15, c - 55))

    img = img.filter(ImageFilter.GaussianBlur(0.5))
    return img, (board_x, board_y, board_w, board_h)


# ---------------------------------------------------------------------
# STATS CARD
# ---------------------------------------------------------------------

def rounded_card(size, radius=28, fill=(251, 248, 242, 235)):
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=fill)
    return card


def build_stats_card(location, date, tides_today, tide_window, tide_8am, tide_5pm, hours, buoy):
    cw, ch = 460, 430
    shadow = Image.new("RGBA", (cw + 40, ch + 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([20, 24, 20 + cw, 24 + ch], radius=28, fill=(10, 20, 16, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))

    card = rounded_card((cw, ch))
    draw = ImageDraw.Draw(card)
    pad = 26

    y = pad
    draw.text((pad, y), location["name"].upper(), font=ImageFont.truetype(F_MONO_BOLD, 16), fill=TEAL)
    y += 22
    draw.text((pad, y), date.strftime("%A, %B %-d"), font=ImageFont.truetype(F_MONO, 14), fill=INK)
    y += 32

    # mini tide curve
    curve_h = 70
    t0 = tides_today[0]["time"].replace(hour=0, minute=0)
    pts = []
    for m in range(0, 24 * 60, 20):
        t = t0 + datetime.timedelta(minutes=m)
        interp = interpolate_tide(tide_window, t)
        hgt = interp["height"] if interp else tides_today[0]["height"]
        px = pad + (m / (24 * 60)) * (cw - 2 * pad)
        pts.append((px, hgt))
    heights = [p[1] for p in pts]
    hmin, hmax = min(heights), max(heights)
    span = max(hmax - hmin, 0.1)
    poly = [(px, y + curve_h - (hh - hmin) / span * curve_h) for px, hh in pts]
    draw.line(poly, fill=INK, width=3, joint="curve")
    for e in tides_today:
        px = pad + ((e["time"] - t0).total_seconds() / 86400) * (cw - 2 * pad)
        py = y + curve_h - (e["height"] - hmin) / span * curve_h
        draw.ellipse([px - 4, py - 4, px + 4, py + 4], fill=ACCENT)
    y += curve_h + 14

    tline = ""
    if tide_8am:
        tline += f"8A {tide_8am['height']:.1f}ft{'↑' if tide_8am['rising'] else '↓'}   "
    if tide_5pm:
        tline += f"5P {tide_5pm['height']:.1f}ft{'↑' if tide_5pm['rising'] else '↓'}"
    draw.text((pad, y), tline, font=ImageFont.truetype(F_MONO_BOLD, 20), fill=ACCENT)
    y += 30
    hi_lo = "  ".join(
        f"{'Hi' if e['type']=='H' else 'Lo'} {e['time'].strftime('%-I:%M%p').lower()} {e['height']:.1f}ft"
        for e in tides_today
    )
    draw.text((pad, y), hi_lo, font=ImageFont.truetype(F_MONO, 13), fill=INK)
    y += 30
    draw.line([(pad, y), (cw - pad, y)], fill=SAND, width=2)
    y += 18

    # condensed hourly (4 points)
    subset = hours[::max(len(hours) // 4, 1)][:4]
    col_w = (cw - 2 * pad) / len(subset)
    icon_y = y + 34
    for i, (th, period, t) in enumerate(subset):
        cx = pad + col_w * i + col_w / 2
        draw.text((cx - 14, y), t.strftime("%-I%p").lower(), font=ImageFont.truetype(F_MONO, 13), fill=INK)
        sky_icon(draw, cx, icon_y, 14, sky_glyph(period["shortForecast"]))
        draw_centered(draw, cx, icon_y + 20, f"{period['temperature']}°", ImageFont.truetype(F_SANS_BOLD, 16), INK)
        wdir = period["windDirection"]
        deg = DIR_TO_DEG.get(wdir, 0)
        draw_wind_arrow(draw, cx, icon_y + 52, deg, 11, TEAL)
    y = icon_y + 68

    draw.line([(pad, y), (cw - pad, y)], fill=SAND, width=2)
    y += 16
    cat, _ = chop_category(buoy["wave_ft"] if buoy else None)
    if buoy:
        chop_line = f"CHOP  {cat} · {buoy['wave_ft']}ft"
        if buoy["wspd_kt"]:
            chop_line += f"  ·  wind {buoy['wspd_kt']:.0f}kt"
    else:
        chop_line = "CHOP  unavailable"
    draw.text((pad, y), chop_line, font=ImageFont.truetype(F_MONO_BOLD, 15), fill=INK)
    y += 26
    tsw = datetime.datetime.now().strftime("%-I:%M%p")
    draw.text((pad, y), f"NOAA · NWS · NDBC 44020  ·  upd {tsw}",
              font=ImageFont.truetype(F_MONO, 11), fill=(140, 140, 130))

    return shadow, card


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    now = datetime.datetime.now()
    yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")
    tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y%m%d")
    location = LOCATIONS[now.timetuple().tm_yday % 3]

    tide_window = fetch_tides(yesterday_str, tomorrow_str)
    tides_today = [e for e in tide_window if e["time"].date() == now.date()]
    tide_8am = interpolate_tide(tide_window, now.replace(hour=8, minute=0, second=0, microsecond=0))
    tide_5pm = interpolate_tide(tide_window, now.replace(hour=17, minute=0, second=0, microsecond=0))
    periods = fetch_hourly_weather()
    hours = pick_hours(periods)
    buoy = fetch_buoy()

    vibe, category, trend = pick_vibe_line(hours, now)
    print(f"Vibe line ({category}/{trend}): {vibe!r}")

    scene, (bx, by, bw, bh) = full_beach_scene((W, H))
    img = scene.convert("RGBA")

    # chalk vibe line on the board, auto-fit to board width
    inner_w = bw - 70
    lines = vibe.split("\n")
    total_h = 0
    rendered = []
    for i, l in enumerate(lines):
        target = 54 if i == 0 else 46
        sz = fit_size(l, target, inner_w)
        layer = chalk_text(l, sz, rotation=random.uniform(-1.5, 1.5))
        rendered.append(layer)
        total_h += layer.size[1] * 0.75
    start_y = by + bh / 2 - total_h / 2
    cy = start_y
    for layer in rendered:
        paste(img, layer, (bx + bw / 2, cy), anchor="ma")
        cy += layer.size[1] * 0.75
    date_tag = chalk_text(now.strftime("%a %-m/%-d"), 22, grain=0.28, rotation=-2)
    paste(img, date_tag, (bx + bw - 20, by + bh - 34), anchor="ra")

    # stats card, bottom-right corner
    shadow, card = build_stats_card(location, now, tides_today, tide_window,
                                      tide_8am, tide_5pm, hours, buoy)
    card_x = W - card.size[0] - 44
    card_y = H - card.size[1] - 60
    img.alpha_composite(shadow, (card_x - 20, card_y - 24))
    img.alpha_composite(card, (card_x, card_y))

    # location tag, top-left
    draw = ImageDraw.Draw(img)
    tag = f"{location['name']} · {location['sub']}"
    draw.text((40, 36), tag, font=ImageFont.truetype(F_SANS_BOLD, 24), fill=(255, 255, 255))
    draw.text((40, 36), tag, font=ImageFont.truetype(F_SANS_BOLD, 24), fill=(255, 255, 255))

    out = img.convert("RGB")
    path = f"daily_guide_v2_{now.strftime('%Y%m%d')}.png"
    out.save(path)
    print("saved", path)


if __name__ == "__main__":
    main()
