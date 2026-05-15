# 🌍 MYLN EARTH MONITOR

> Real-time satellite tracking + earthquake alerts — powered by MYLN-FRAME.  
> No subscriptions. No API keys. Just data, speed, and a little bit of space-center romance.

![screenshot](https://img.shields.io/badge/status-live-00ff88?style=flat&logo=satellite&logoColor=white)
![myln](https://img.shields.io/badge/MYLN--FRAME-EarthquakeHead-00cfff?style=flat)
![license](https://img.shields.io/badge/license-MIT-blue?style=flat)

---

## What it looks like

```
┌──────────────────────────────────────────────────────┐
│  ⚡ MYLN EARTH MONITOR     2026-05-15  20:17:42      │
├──────────────────────────────────────────────────────┤
│                                                      │
│   🌍  Mercator world map                             │
│        🛸 Satellites moving in real-time             │
│        🔴 Earthquake epicenters blinking             │
│                                                      │
├────────────────────────┬─────────────────────────────┤
│  🛸 SATELLITE TRACK    │  🚨 EARTHQUAKE ALERT ≥ 震度4 │
│  ISS  408km            │  ⚠️  M5.2 岩手沖  HIGH 83%  │
│  ...                   │     [blinking red dot]       │
└────────────────────────┴─────────────────────────────┘
```

Dark terminal aesthetic. Green glow. Blinking alerts. The way Earth monitoring should look.

---

## Features

- **🌍 Mercator world map** — TopoJSON countries, latitude/longitude grid
- **🛸 Live satellite positions** — ISS + visual satellites via CelesTrak / sgp4
- **🚨 Earthquake alerts** — Japan JMA data, 震度4 (seismic intensity 4) and above only
- **⚡ MYLN-FRAME AI** — `EarthquakeHead` classifies each quake: SAFE / LOW / MEDIUM / HIGH / CRITICAL
- **🕐 Real-time clock** — UTC+local, always ticking
- **🔄 Auto-refresh** — every 30 seconds, no page reload

---

## MYLN Integration

This project uses [MYLN-FRAME](https://github.com/Sub-to/myln-frame) as its AI brain.

The `EarthquakeHead` is an **ultra-light specialist head** (~0.1 µs/inference):

```
Input (5-dim):
  [0] intensity  — seismic intensity / 7.0
  [1] magnitude  — Richter / 9.0
  [2] depth_inv  — 1 - depth/700km  (shallow = dangerous)
  [3] tsunami    — tsunami warning flag (0 or 1)
  [4] freq       — recent quake frequency / 10

Output: SAFE · LOW · MEDIUM · HIGH · CRITICAL
```

No training. No GPU. No cloud. Just hand-tuned weights and physics.

```
Earthquake event
      ↓
  EarthquakeHead × 4  (~0.1 µs each)
      ↓
  Ring Attention  (lateral signal sharing)
      ↓
  CENTER LINE  (threshold classification)
      ↓
  SAFE / LOW / MEDIUM / HIGH / CRITICAL
```

---

## Free APIs used

| Data | Source | Key needed? |
|---|---|---|
| 🌍 World map | [world-atlas](https://github.com/topojson/world-atlas) CDN | ✗ |
| 🛸 Satellite TLE | [CelesTrak](https://celestrak.org) | ✗ |
| 🛸 ISS position | [Open Notify](http://api.open-notify.org) | ✗ |
| 🚨 Earthquakes | [P2P地震情報](https://www.p2pquake.net/develop/json_api_v2/) | ✗ |

100% free. 100% open. Runs entirely on your own hardware.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/Sub-to/myln-earth-monitor.git
cd myln-earth-monitor

# 2. Install
pip install -r requirements.txt

# 3. (Optional) Build MYLN-FRAME for AI classification
git clone https://github.com/Sub-to/myln-frame.git /tmp/myln-frame
cd /tmp/myln-frame && mkdir build && cd build && cmake .. && make myln
cd -

# 4. Run
python3 server.py

# 5. Open browser
open http://localhost:5050
```

Works without MYLN-FRAME too — falls back to rule-based classification.

---

## Architecture

```
server.py          — Flask backend: API proxy + MYLN integration
static/index.html  — Frontend: Canvas map + D3 TopoJSON + real-time UI

MYLN stack:
  myln-frame/heads/earthquake_head.h   — ultra-light head (~0.1µs)
  myln-frame/tuner/earthquake_tuner.h  — weight configuration
```

---

## Why

Because watching satellites orbit Earth in real-time while earthquake alerts blink on a dark terminal map is just... cool.

And because AI that protects people should run on *their* hardware — not a server farm somewhere.

*Part of the [MYLN-FRAME](https://github.com/Sub-to/myln-frame) project.*  
*Human–AI coexistence, one microsecond at a time.*

---

## License

MIT
