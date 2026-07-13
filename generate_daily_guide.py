#!/usr/bin/env python3
"""
Daily Weather + Tide Guide generator.
Pulls live data from NOAA CO-OPS, NWS, and NDBC, renders a single
portrait image summarizing tides, hourly weather, wind, and sea state
for a Cape Cod location (default: Red River Beach, Harwichport, MA).

Run standalone: python3 generate_daily_guide.py
Output: daily_guide_<date>.png
"""

import os
import json
import math
import datetime
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

LOCATIONS = [
    {"name": "Red River Beach", "sub": "Harwichport, MA", "scene": "beach"},
    {"name": "Saquatucket Harbor", "sub": "Harwichport, MA", "scene": "harbor"},
    {"name": "Allen Harbor", "sub": "Harwichport, MA", "scene": "marsh"},
]

LAT, LON = 41.6685, -70.0762           # Red River Beach
TIDE_STATION = "8447506"                # Wychmere Harbor, Harwich Port (subordinate)
BUOY_STATION = "44020"                  # Nantucket Sound
UA = {"User-Agent": "(pendrill-daily-weather-guide, andrew@pendrill.com)"}

FONT_DIR = os.environ.get("DAILY_GUIDE_FONT_DIR", "/usr/share/fonts/truetype/dejavu/")
F_SERIF      = FONT_DIR + "DejaVuSerif.ttf"
F_SERIF_BOLD = FONT_DIR + "DejaVuSerif-Bold.ttf"
F_SANS       = FONT_DIR + "DejaVuSans.ttf"
F_SANS_BOLD  = FONT_DIR + "DejaVuSans-Bold.ttf"
F_MONO       = FONT_DIR + "DejaVuSansMono.ttf"
F_MONO_BOLD  = FONT_DIR + "DejaVuSansMono-Bold.ttf"

W, H = 1080, 1620

# Palette
INK       = (27, 58, 92)
TEAL      = (58, 108, 112)
ACCENT    = (193, 80, 46)
CREAM     = (251, 248, 242)
SAND      = (232, 217, 181)
SKY_TOP   = (156, 202, 224)
SKY_BOT   = (233, 224, 192)
WATER     = (62, 116, 118)
WATER_DK  = (44, 90, 94)

DIR_TO_DEG = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
    "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225,
    "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


# ---------------------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------------------

def fetch_tides(begin_date_str, end_date_str):
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    params = {
        "product": "predictions", "application": "pendrill_weather_guide",
        "begin_date": begin_date_str, "end_date": end_date_str, "datum": "MLLW",
        "station": TIDE_STATION, "time_zone": "lst_ldt", "units": "english",
        "interval": "hilo", "format": "json",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    events = []
    for p in data.get("predictions", []):
        t = datetime.datetime.strptime(p["t"], "%Y-%m-%d %H:%M")
        events.append({"time": t, "height": float(p["v"]), "type": p["type"]})
    return sorted(events, key=lambda e: e["time"])


def interpolate_tide(events, target_dt):
    """Cosine interpolation between the two hi/lo events bracketing target_dt.
    Standard approximation for subordinate stations, which only publish
    hi/lo predictions (no continuous curve). Not navigation-grade."""
    before = [e for e in events if e["time"] <= target_dt]
    after = [e for e in events if e["time"] >= target_dt]
    if not before or not after:
        return None
    e0, e1 = before[-1], after[0]
    if e0["time"] == e1["time"]:
        return e0["height"]
    span = (e1["time"] - e0["time"]).total_seconds()
    elapsed = (target_dt - e0["time"]).total_seconds()
    frac = elapsed / span
    h = e0["height"] + (e1["height"] - e0["height"]) * (1 - math.cos(math.pi * frac)) / 2
    rising = e1["height"] > e0["height"]
    return {"height": round(h, 1), "rising": rising}


def fetch_hourly_weather():
    pt = requests.get(f"https://api.weather.gov/points/{LAT},{LON}", headers=UA, timeout=15)
    pt.raise_for_status()
    hourly_url = pt.json()["properties"]["forecastHourly"]
    fc = requests.get(hourly_url, headers=UA, timeout=15)
    fc.raise_for_status()
    return fc.json()["properties"]["periods"]


def pick_hours(periods, target_hours=(6, 9, 12, 15, 18, 20)):
    """Pick the forecast periods closest to today's target local hours."""
    today = datetime.datetime.now().date()
    picked = []
    for th in target_hours:
        best = None
        for p in periods:
            t = datetime.datetime.fromisoformat(p["startTime"])
            if t.date() != today:
                continue
            diff = abs(t.hour - th)
            if best is None or diff < best[0]:
                best = (diff, p, t)
        if best:
            picked.append((th, best[1], best[2]))
    return picked


def fetch_buoy():
    r = requests.get(f"https://www.ndbc.noaa.gov/data/realtime2/{BUOY_STATION}.txt", timeout=15)
    r.raise_for_status()
    lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
    for line in lines:
        cols = line.split()
        # YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
        wdir, wspd, gst, wvht, dpd = cols[5], cols[6], cols[7], cols[8], cols[9]
        if wvht != "MM":
            return {
                "wdir_deg": None if wdir == "MM" else float(wdir),
                "wspd_kt": None if wspd == "MM" else round(float(wspd) * 1.94384, 1),
                "gust_kt": None if gst == "MM" else round(float(gst) * 1.94384, 1),
                "wave_ft": round(float(wvht) * 3.28084, 1),
                "period_s": None if dpd == "MM" else float(dpd),
            }
    return None


def chop_category(wave_ft):
    if wave_ft is None:
        return "Unknown", 0.5
    if wave_ft < 1.0:
        return "Calm", 0.12
    if wave_ft < 2.0:
        return "Light chop", 0.38
    if wave_ft < 3.5:
        return "Moderate chop", 0.65
    return "Rough", 0.9


def sky_glyph(short_forecast):
    s = short_forecast.lower()
    if "thunder" in s:
        return "storm"
    if "rain" in s or "shower" in s:
        return "rain"
    if "snow" in s:
        return "snow"
    if "fog" in s:
        return "fog"
    if "cloud" in s or "overcast" in s:
        return "cloud"
    return "sun"


# ---------------------------------------------------------------------
# DRAWING HELPERS
# ---------------------------------------------------------------------

def font(path, size):
    return ImageFont.truetype(path, size)


def text_w(draw, txt, fnt):
    b = draw.textbbox((0, 0), txt, font=fnt)
    return b[2] - b[0]


def draw_centered(draw, cx, y, txt, fnt, fill):
    w = text_w(draw, txt, fnt)
    draw.text((cx - w / 2, y), txt, font=fnt, fill=fill)


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def draw_vgradient(draw, box, c_top, c_bot):
    x0, y0, x1, y1 = box
    height = y1 - y0
    for i in range(height):
        t = i / max(height - 1, 1)
        draw.line([(x0, y0 + i), (x1, y0 + i)], fill=lerp_color(c_top, c_bot, t))


def draw_wind_arrow(draw, cx, cy, deg, size, color):
    """Arrow points in the direction the wind is blowing TOWARD (meteorological
    convention flipped for readability: shows travel direction, tip = downwind)."""
    rad = math.radians(deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    tip = (cx + dx * size, cy + dy * size)
    tail = (cx - dx * size, cy - dy * size)
    perp = (-dy, dx)
    left = (tip[0] - dx * size * 0.6 + perp[0] * size * 0.35,
            tip[1] - dy * size * 0.6 + perp[1] * size * 0.35)
    right = (tip[0] - dx * size * 0.6 - perp[0] * size * 0.35,
             tip[1] - dy * size * 0.6 - perp[1] * size * 0.35)
    draw.line([tail, tip], fill=color, width=3)
    draw.polygon([tip, left, right], fill=color)


def sky_icon(draw, cx, cy, r, kind):
    if kind == "sun":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(232, 163, 61))
        for a in range(0, 360, 45):
            rad = math.radians(a)
            x0, y0 = cx + math.cos(rad) * r * 1.3, cy + math.sin(rad) * r * 1.3
            x1, y1 = cx + math.cos(rad) * r * 1.7, cy + math.sin(rad) * r * 1.7
            draw.line([(x0, y0), (x1, y1)], fill=(232, 163, 61), width=2)
    elif kind in ("cloud", "fog"):
        col = (150, 165, 175) if kind == "fog" else (170, 180, 188)
        draw.ellipse([cx - r, cy - r * 0.4, cx + r * 0.4, cy + r * 0.6], fill=col)
        draw.ellipse([cx - r * 0.3, cy - r * 0.7, cx + r, cy + r * 0.4], fill=col)
    elif kind == "rain":
        sky_icon(draw, cx, cy - r * 0.3, r * 0.8, "cloud")
        for i in range(-1, 2):
            x = cx + i * r * 0.5
            draw.line([(x, cy + r * 0.4), (x - 3, cy + r * 1.1)], fill=(78, 124, 130), width=2)
    elif kind == "storm":
        sky_icon(draw, cx, cy - r * 0.3, r * 0.8, "cloud")
        draw.polygon([(cx, cy + r * 0.3), (cx - 8, cy + r * 0.9),
                      (cx + 2, cy + r * 0.9), (cx - 6, cy + r * 1.5)],
                     fill=(193, 80, 46))
    else:
        sky_icon(draw, cx, cy, r, "cloud")


def scene_illustration(size, scene):
    """Procedural placeholder background. Swap this for a real photo pipeline
    (your own library, rotated daily) once you've got images to feed it."""
    img = Image.new("RGB", size, SKY_TOP)
    draw = ImageDraw.Draw(img)
    w, h = size
    horizon = int(h * 0.62)
    draw_vgradient(draw, (0, 0, w, horizon), SKY_TOP, SKY_BOT)
    draw_vgradient(draw, (0, horizon, w, h), WATER, WATER_DK)

    # sun
    draw.ellipse([w - 220, 60, w - 100, 180], fill=(247, 214, 150))

    if scene == "beach":
        draw.ellipse([-100, horizon - 40, w * 0.5, h + 200], fill=SAND)
        for i in range(40):
            x = 60 + i * 22
            y = horizon - 60 - (i % 5) * 6
            draw.line([(x, y + 30), (x - 4, y)], fill=(120, 140, 90), width=3)
    elif scene == "harbor":
        for i, x in enumerate([w * 0.25, w * 0.45, w * 0.68]):
            y = horizon + 40 + i * 18
            draw.polygon([(x, y), (x + 70, y), (x + 55, y - 34)], fill=(235, 232, 224))
            draw.line([(x + 35, y - 34), (x + 35, y - 90)], fill=(90, 90, 90), width=3)
        draw.rectangle([0, horizon - 6, w, horizon + 6], fill=(120, 110, 95))
    else:  # marsh
        for i in range(60):
            x = (i * 37) % w
            y = horizon + 10 + (i % 7) * 8
            draw.line([(x, y + 40), (x - 3, y)], fill=(150, 160, 90), width=2)
        draw.ellipse([-150, horizon - 20, w * 0.4, h + 150], fill=(190, 178, 140))

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img


# ---------------------------------------------------------------------
# MAIN RENDER
# ---------------------------------------------------------------------

def render(date, location, tides_today, tide_window, tide_8am, tide_5pm, hours, buoy):
    img = Image.new("RGB", (W, H), CREAM)
    draw = ImageDraw.Draw(img)

    # --- zone 1: scene band ---
    band_h = 560
    scene = scene_illustration((W, band_h), location["scene"])
    img.paste(scene, (0, 0))
    overlay = Image.new("RGBA", (W, 210), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for i in range(210):
        a = int(160 * (i / 210))
        odraw.line([(0, i), (W, i)], fill=(15, 30, 45, a))
    img.paste(Image.alpha_composite(
        img.crop((0, band_h - 210, W, band_h)).convert("RGBA"), overlay
    ), (0, band_h - 210))
    draw = ImageDraw.Draw(img)

    draw.text((44, band_h - 168), location["name"], font=font(F_SERIF_BOLD, 46), fill=CREAM)
    draw.text((44, band_h - 112), f"{location['sub']} · {date.strftime('%A, %B %-d')}",
               font=font(F_MONO, 20), fill=(225, 230, 232))
    midday = next((h for h in hours if h[0] == 12), hours[len(hours) // 2])
    temps = [h[1]["temperature"] for h in hours]
    summary = f"{midday[1]['shortForecast']} · high {max(temps)}\u00b0, low {min(temps)}\u00b0"
    draw.text((44, band_h - 68), summary, font=font(F_SANS, 21), fill=(240, 235, 220))

    # --- card panel ---
    y = band_h
    draw.rectangle([0, y, W, H], fill=CREAM)

    pad = 44
    y += 34
    draw.text((pad, y), "TIDE, NEXT 24H", font=font(F_MONO_BOLD, 17), fill=TEAL)
    y += 38

    # tide curve — interpolated from the full tide_window so the line has
    # real hi/lo points to bracket against near midnight on both ends
    curve_h = 180
    curve_top = y
    pts = []
    t0 = tides_today[0]["time"].replace(hour=0, minute=0)
    for m in range(0, 24 * 60, 15):
        t = t0 + datetime.timedelta(minutes=m)
        interp = interpolate_tide(tide_window, t)
        hgt = interp["height"] if interp else tides_today[0]["height"]
        px = pad + (m / (24 * 60)) * (W - 2 * pad)
        pts.append((px, hgt))
    heights = [p[1] for p in pts]
    hmin, hmax = min(heights), max(heights)
    span = max(hmax - hmin, 0.1)
    poly = [(px, curve_top + curve_h - (h - hmin) / span * curve_h) for px, h in pts]
    draw.line(poly, fill=INK, width=3, joint="curve")

    for e in tides_today:
        px = pad + ((e["time"] - t0).total_seconds() / 86400) * (W - 2 * pad)
        py = curve_top + curve_h - (e["height"] - hmin) / span * curve_h
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=ACCENT)
        label = f"{'High' if e['type']=='H' else 'Low'} {e['time'].strftime('%-I:%M%p').lower()} · {e['height']:.1f}ft"
        anchor_y = py - 30 if e["type"] == "H" else py + 14
        draw_centered(draw, px, anchor_y, label, font(F_MONO, 15), INK)

    for label_time, hour in (("8:00A", 8), ("5:00P", 17)):
        px = pad + (hour / 24) * (W - 2 * pad)
        draw.line([(px, curve_top), (px, curve_top + curve_h)], fill=ACCENT, width=1)

    y = curve_top + curve_h + 20
    for label, data in (("8:00 AM", tide_8am), ("5:00 PM", tide_5pm)):
        if data:
            arrow = "↑" if data["rising"] else "↓"
            txt = f"{label} → {data['height']:.1f} ft {arrow} (interpolated)"
        else:
            txt = f"{label} → n/a"
        draw.text((pad, y), txt, font=font(F_MONO_BOLD, 18), fill=ACCENT)
        y += 26

    y += 16
    draw.line([(pad, y), (W - pad, y)], fill=SAND, width=2)
    y += 26

    # hourly + wind
    draw.text((pad, y), "HOURLY WEATHER + WIND", font=font(F_MONO_BOLD, 17), fill=TEAL)
    y += 44
    col_w = (W - 2 * pad) / len(hours)
    for i, (th, period, t) in enumerate(hours):
        cx = pad + col_w * i + col_w / 2
        draw.text((cx - 16, y), t.strftime("%-I%p").lower(), font=font(F_MONO, 16), fill=INK)
        sky_icon(draw, cx, y + 62, 24, sky_glyph(period["shortForecast"]))
        temp_txt = f"{period['temperature']}°"
        draw_centered(draw, cx, y + 100, temp_txt, font(F_SANS_BOLD, 25), INK)
        wspd = period["windSpeed"].split()[0]
        wdir = period["windDirection"]
        deg = DIR_TO_DEG.get(wdir, 0)
        draw_wind_arrow(draw, cx, y + 160, deg, 18, TEAL)
        draw_centered(draw, cx, y + 184, f"{wspd}kt {wdir}", font(F_MONO, 14), TEAL)

    y += 226
    draw.line([(pad, y), (W - pad, y)], fill=SAND, width=2)
    y += 26

    # sea state
    draw.text((pad, y), "SEA STATE", font=font(F_MONO_BOLD, 17), fill=TEAL)
    y += 44
    cat, frac = chop_category(buoy["wave_ft"] if buoy else None)
    gauge_x0, gauge_x1 = pad, W - pad
    steps = [(0.0, (191, 227, 216)), (0.33, (244, 226, 166)),
             (0.66, (240, 180, 140)), (1.0, (224, 138, 110))]
    seg_w = (gauge_x1 - gauge_x0) / (len(steps) - 1)
    for i in range(len(steps) - 1):
        x0 = gauge_x0 + i * seg_w
        x1 = gauge_x0 + (i + 1) * seg_w
        draw.rectangle([x0, y, x1, y + 18], fill=lerp_color(steps[i][1], steps[i + 1][1], 0.5))
    needle_x = gauge_x0 + frac * (gauge_x1 - gauge_x0)
    draw.polygon([(needle_x - 9, y - 12), (needle_x + 9, y - 12), (needle_x, y + 10)], fill=INK)
    y += 38
    for lbl, xf in (("Calm", 0), ("Light chop", 0.33), ("Moderate", 0.66), ("Rough", 1.0)):
        draw_centered(draw, gauge_x0 + xf * (gauge_x1 - gauge_x0), y, lbl.upper(), font(F_MONO, 12), INK)
    y += 40
    if buoy:
        detail = f"{cat} · Buoy {BUOY_STATION}: {buoy['wave_ft']} ft"
        if buoy["period_s"]:
            detail += f" @ {buoy['period_s']:.0f}s"
        if buoy["wspd_kt"]:
            detail += f", wind {buoy['wspd_kt']:.0f}kt"
    else:
        detail = "Buoy data unavailable"
    draw.text((pad, y), detail, font=font(F_SANS_BOLD, 19), fill=INK)
    y += 30
    draw.text((pad, y), "Nearest buoy is ~8mi offshore; treat as Sound-wide sea state, not a Red River read.",
               font=font(F_MONO, 13), fill=(120, 120, 110))

    # footer
    foot_h = 54
    draw.rectangle([0, H - foot_h, W, H], fill=INK)
    draw.text((pad, H - foot_h + 16),
              "NOAA CO-OPS · NWS · NDBC 44020", font=font(F_MONO, 13), fill=CREAM)
    ts = datetime.datetime.now().strftime("%-I:%M %p ET")
    tsw = text_w(draw, f"updated {ts}", font(F_MONO, 13))
    draw.text((W - pad - tsw, H - foot_h + 16), f"updated {ts}", font=font(F_MONO, 13), fill=CREAM)

    return img


def main():
    now = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")
    yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")
    tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y%m%d")
    location = LOCATIONS[now.timetuple().tm_yday % 3]

    print(f"Location: {location['name']}")
    # Pull a 3-day window so the curve has real hi/lo points to bracket
    # against near midnight, not just today's events.
    tide_window = fetch_tides(yesterday_str, tomorrow_str)
    tides_today = [e for e in tide_window if e["time"].date() == now.date()]
    print(f"Tide events today: {[(e['type'], e['time'].strftime('%H:%M'), e['height']) for e in tides_today]}")

    tide_8am = interpolate_tide(tide_window, now.replace(hour=8, minute=0, second=0, microsecond=0))
    tide_5pm = interpolate_tide(tide_window, now.replace(hour=17, minute=0, second=0, microsecond=0))
    print("8am:", tide_8am, " 5pm:", tide_5pm)

    periods = fetch_hourly_weather()
    hours = pick_hours(periods)
    print(f"Picked {len(hours)} hourly slots")

    buoy = fetch_buoy()
    print("Buoy:", buoy)

    img = render(now, location, tides_today, tide_window, tide_8am, tide_5pm, hours, buoy)
    out_path = f"daily_guide_{date_str}.png"
    img.save(out_path)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
