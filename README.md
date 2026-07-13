# Red River Beach daily guide

Generates a daily image: tide list, hourly weather, onshore/offshore wind
read with a kite verdict, sea state, a condition-matched real photo, and a
chalk-style vibe line. Data comes live from NOAA CO-OPS, NWS, and NDBC —
nothing here is cached or fake.

Run it directly:
```
pip install -r requirements.txt
python3 generate_daily_guide_v3.py
```
Output is a PNG named `daily_guide_v3_YYYYMMDD.png` in the working directory.

## Getting this to run every morning, unattended

Cloud Scheduled Tasks (via `/schedule`) run on Anthropic's infrastructure,
so your machine can be off. The catch: cloud tasks clone a fresh copy of a
repo on every run and can't see local files, so this folder needs to live
in a git repo Claude Code can reach — that's why the fonts and photos are
bundled here instead of referencing anywhere on disk.

**Setup, one time:**
1. Push this folder to a GitHub repo (private is fine).
2. In Claude Code, pointed at that repo, run:

   ```
   /schedule every day at 7:00am: run generate_daily_guide_v3.py, then
   [deliver it — see options below]
   ```

3. Claude Code will likely ask you to authorize a connector (Google Drive
   and/or Gmail) the first time — that's expected, approve it once.
4. Let the first run happen supervised, not overnight blind. Cloud
   containers don't always ship with the DejaVu fonts the script assumes
   are already installed (`/usr/share/fonts/truetype/dejavu/`) — if that's
   missing, Claude Code can just `apt-get install fonts-dejavu-core`
   itself since it's agentic, not a static cron job, but you want to see
   that happen once rather than assume it.

**Delivery, digital only — pick one when you write the `/schedule` prompt:**

- **Google Drive (most reliable):** "...then save the PNG to my Drive
  folder [name it]." Shows up in Drive/Photos on your phone automatically
  if that folder syncs.
- **Email:** "...then email me the PNG at [address]." Simple, lands as a
  notification. Worth confirming during setup whether Claude Code sends it
  outright or leaves it as a draft for you to tap send on — that depends
  on how the Gmail connector is scoped in your account.
- **Wallpaper, automatically:** more setup than the above. Requires
  hosting the PNG at a stable URL (Netlify works, and you've already got
  that connector) plus an iOS Shortcuts automation on your phone that
  fetches it each morning and sets it as wallpaper. Worth doing once the
  first two are working and you know you want to keep this long-term.

## Known limitations, worth remembering

- 8am/5pm tide heights are interpolated (station 8447506 is a subordinate
  station — NOAA only publishes hi/lo for it), not raw readings.
- Sea state comes from Buoy 44020, ~8mi offshore — Sound-wide conditions,
  not a Red River Beach-specific read.
- Wind verdict is anchored to midday; the color-coded hourly arrows are
  there to catch a shift the noon snapshot would miss.
- Photo library only covers Red River Beach and only has 4 photos across
  4 condition categories. Panel/card placement was pixel-verified against
  `bright.jpeg` only — the other three use estimated placement fractions.
