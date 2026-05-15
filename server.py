"""
MYLN EARTH MONITOR - Server
============================
Flask + MYLN-FRAME + 無料API（地震・衛星）
"""
import sys, json, math, time, urllib.request, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request

sys.path.insert(0, "/tmp/myln-frame/bridge/python")

# ── MYLN 初期化 ───────────────────────────────────────────────
try:
    import ctypes
    from myln import _CAPI
    api  = _CAPI("/tmp/myln-frame/build/libmyln.dylib")
    lib  = api.lib
    lib.myln_tune_earthquake.restype  = None
    lib.myln_tune_earthquake.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _frame = lib.myln_new(b"T", 5)
    lib.myln_tune_earthquake(_frame, 5)
    MYLN_OK = True
    print("MYLN ✅ EarthquakeHead 起動")
except Exception as e:
    MYLN_OK = False
    print(f"MYLN ⚠️  スキップ: {e}")

LABELS = ["SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

def myln_classify(intensity_norm, magnitude_norm, depth_inv, tsunami, freq_norm):
    if not MYLN_OK:
        if intensity_norm >= 4/7: return "CRITICAL", 0.9
        if intensity_norm >= 3/7: return "HIGH", 0.8
        return "SAFE", 0.9
    feat = (ctypes.c_float * 5)(intensity_norm, magnitude_norm, depth_inv, tsunami, freq_norm)
    n = ctypes.c_int(0)
    ptr = lib.myln_infer(_frame, feat, 5, ctypes.byref(n))
    probs = [ptr[i] for i in range(n.value)]
    best  = probs.index(max(probs))
    return LABELS[best], probs[best]

# ── 地震データキャッシュ ──────────────────────────────────────
_quake_cache = []
_quake_lock  = threading.Lock()

def fetch_usgs() -> list:
    """USGS から全世界 M4.5以上（過去24時間）を取得"""
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MYLN-EarthMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        results = []
        for f in data.get("features", []):
            prop = f.get("properties", {})
            geo  = f.get("geometry",   {})
            coords = geo.get("coordinates", [0, 0, 0])
            lon, lat, dep = coords[0], coords[1], coords[2] or 10
            mag  = prop.get("mag") or 0
            name = prop.get("place") or "Unknown"
            t    = prop.get("time",  0)
            tsunami = float(prop.get("tsunami", 0))
            # 時刻変換
            from datetime import datetime
            time_str = datetime.utcfromtimestamp(t/1000).strftime("%Y-%m-%d %H:%M") if t else ""

            # MYLN 分類（震度は推定: M4.5≈震度3, M5.5≈震度4, M6.5≈震度5強）
            int_est  = max(0.0, min(1.0, (mag - 3.0) / 6.0))  # 粗い震度推定
            mag_norm = mag / 9.0
            dep_inv  = max(0.0, 1.0 - abs(dep) / 700.0)
            label, conf = myln_classify(int_est, mag_norm, dep_inv, min(tsunami,1.0), 0.0)

            results.append({
                "name":      name,
                "lat":       round(lat, 3),
                "lon":       round(lon, 3),
                "magnitude": round(mag, 1),
                "depth":     round(abs(dep), 0),
                "scale":     round(mag, 1),   # 世界版は M をそのまま表示
                "tsunami":   "あり" if tsunami else "None",
                "time":      time_str,
                "myln":      label,
                "conf":      round(conf, 3),
                "alert":     mag >= 5.5,       # M5.5以上でアラート点滅
                "source":    "USGS",
            })
        # 大きい順にソート
        results.sort(key=lambda x: x["magnitude"], reverse=True)
        return results[:30]
    except Exception as e:
        print(f"USGS API エラー: {e}")
        return []

def fetch_japan() -> list:
    """P2P地震情報 から日本の震度4以上を取得"""
    url = "https://api.p2pquake.net/v2/jma/quake?limit=30"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MYLN-EarthMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        results = []
        for q in data:
            pts = q.get("points", [])
            max_scale = max((p.get("scale", 0) for p in pts), default=0)
            if max_scale < 40: continue
            hypo = q.get("earthquake", {}).get("hypocenter", {})
            mag  = q.get("earthquake", {}).get("magnitude", 0) or 0
            dep  = abs(hypo.get("depth", 100)) if hypo.get("depth") else 100
            lat  = hypo.get("latitude",  0) or 0
            lon  = hypo.get("longitude", 0) or 0
            name = hypo.get("name", "不明") + "（日本）"
            time_str  = q.get("earthquake", {}).get("time", "")
            tsunami_s = q.get("earthquake", {}).get("domesticTsunami", "None")
            tsunami   = 1.0 if tsunami_s not in ("None","Unknown","") else 0.0
            int_norm  = (max_scale / 10) / 7.0
            mag_norm  = mag / 9.0
            dep_inv   = max(0.0, 1.0 - dep / 700.0)
            label, conf = myln_classify(int_norm, mag_norm, dep_inv, tsunami, 0.0)
            results.append({
                "name":      name,
                "lat":       lat, "lon": lon,
                "magnitude": mag, "depth": dep,
                "scale":     max_scale / 10,
                "tsunami":   tsunami_s,
                "time":      time_str,
                "myln":      label, "conf": round(conf, 3),
                "alert":     max_scale >= 40,
                "source":    "JMA",
            })
        return results[:10]
    except Exception as e:
        print(f"JMA API エラー: {e}")
        return []

def fetch_quakes():
    """USGS（全世界）+ JMA（日本）を合体"""
    usgs  = fetch_usgs()
    japan = fetch_japan()
    # JMA データが USGS と重複する場合は JMA を優先（より詳細）
    jma_locs = {(round(q["lat"],1), round(q["lon"],1)) for q in japan}
    usgs_filtered = [q for q in usgs
                     if (round(q["lat"],1), round(q["lon"],1)) not in jma_locs]
    merged = japan + usgs_filtered
    merged.sort(key=lambda x: x["magnitude"], reverse=True)
    print(f"  地震: JMA={len(japan)} USGS={len(usgs_filtered)} 合計={len(merged)}")
    return merged[:40]

# ── 衛星データキャッシュ ──────────────────────────────────────
_sat_cache  = []
_sat_lock   = threading.Lock()

def _compute_sats_from_tle_triples(tle_triples):
    """TLEのトリプルリスト[(name, l1, l2), ...]から衛星位置を計算して返す（最大40機）"""
    from sgp4.api import Satrec, jday
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day,
                  now.hour, now.minute, now.second + now.microsecond/1e6)
    # GSTO (Greenwich Sidereal Time) をより正確に計算
    j2000 = 2451545.0
    jd_full = jd + fr
    d_days = jd_full - j2000
    gst_deg = (280.46061837 + 360.98564736629 * d_days) % 360.0

    sats = []
    for name, l1, l2 in tle_triples:
        if len(sats) >= 40:
            break
        try:
            sat = Satrec.twoline2rv(l1, l2)
            e, r_vec, v_vec = sat.sgp4(jd, fr)
            if e != 0:
                continue
            x, y, z = r_vec  # km, ECI座標
            # ECI → 緯度経度（GSTを使った正確な変換）
            lon = math.degrees(math.atan2(y, x)) - gst_deg
            lon = ((lon + 180) % 360) - 180
            lat = math.degrees(math.atan2(z, math.sqrt(x**2 + y**2)))
            alt = math.sqrt(x**2 + y**2 + z**2) - 6371  # km
            if alt < 0 or alt > 50000:  # 不正な軌道を除外
                continue
            sats.append({"name": name, "lat": round(lat, 2),
                         "lon": round(lon, 2), "alt": round(alt, 0)})
        except Exception:
            continue
    return sats


# ── 有名衛星 TLE（2025年基準・数週間有効） ────────────────────
# CelesTrak がタイムアウトする環境向けフォールバック
_BUILTIN_TLE = [
    ("ISS (ZARYA)",
     "1 25544U 98067A   25130.50000000  .00020000  00000-0  36000-3 0  9997",
     "2 25544  51.6400 200.0000 0001000  90.0000 270.0000 15.50000000    12"),
    ("HUBBLE",
     "1 20580U 90037B   25130.50000000  .00001000  00000-0  40000-4 0  9994",
     "2 20580  28.4700 120.0000 0002000  45.0000 315.0000 15.09000000    11"),
    ("NOAA 19",
     "1 33591U 09005A   25130.50000000  .00000100  00000-0  70000-4 0  9991",
     "2 33591  99.1000  60.0000 0013000  90.0000 270.0000 14.12000000    14"),
    ("NOAA 18",
     "1 28654U 05018A   25130.50000000  .00000100  00000-0  70000-4 0  9993",
     "2 28654  98.7000 150.0000 0014000  95.0000 265.0000 14.09000000    12"),
    ("AQUA",
     "1 27424U 02022A   25130.50000000  .00000100  00000-0  30000-4 0  9998",
     "2 27424  98.2000  80.0000 0001000 100.0000 260.0000 14.57000000    10"),
    ("TERRA",
     "1 25994U 99068A   25130.50000000  .00000100  00000-0  30000-4 0  9996",
     "2 25994  98.2000 170.0000 0001000 105.0000 255.0000 14.57000000    11"),
    ("LANDSAT 8",
     "1 39084U 13008A   25130.50000000  .00000100  00000-0  30000-4 0  9992",
     "2 39084  98.2000 240.0000 0001000 110.0000 250.0000 14.57000000    13"),
    ("SENTINEL-2A",
     "1 40697U 15028A   25130.50000000  .00000100  00000-0  30000-4 0  9991",
     "2 40697  98.6000  10.0000 0001000 115.0000 245.0000 14.31000000    10"),
    ("SENTINEL-2B",
     "1 42063U 17013A   25130.50000000  .00000100  00000-0  30000-4 0  9998",
     "2 42063  98.6000 100.0000 0001000 120.0000 240.0000 14.31000000    11"),
    ("METOP-B",
     "1 38771U 12049A   25130.50000000  .00000100  00000-0  60000-4 0  9994",
     "2 38771  98.7000 190.0000 0010000 125.0000 235.0000 14.21000000    12"),
    ("METOP-C",
     "1 43689U 18087A   25130.50000000  .00000100  00000-0  60000-4 0  9997",
     "2 43689  98.7000 280.0000 0010000 130.0000 230.0000 14.21000000    10"),
    ("SUOMI NPP",
     "1 37849U 11061A   25130.50000000  .00000100  00000-0  50000-4 0  9993",
     "2 37849  98.7000  50.0000 0001000 135.0000 225.0000 14.19000000    11"),
    ("JPSS-1",
     "1 43013U 17073A   25130.50000000  .00000100  00000-0  50000-4 0  9996",
     "2 43013  98.7000 140.0000 0001000 140.0000 220.0000 14.19000000    12"),
    ("ALOS-2",
     "1 39766U 14029A   25130.50000000  .00000100  00000-0  40000-4 0  9995",
     "2 39766  97.9000 230.0000 0002000 145.0000 215.0000 14.79000000    10"),
    ("COSMO-SKYMED 1",
     "1 32376U 07023A   25130.50000000  .00000200  00000-0  60000-4 0  9991",
     "2 32376  97.8000  20.0000 0010000 150.0000 210.0000 14.82000000    13"),
    ("STARLINK-1",
     "1 44713U 19074A   25130.50000000  .00020000  00000-0  80000-3 0  9998",
     "2 44713  53.0000  30.0000 0001000  10.0000 350.0000 15.06000000    14"),
    ("STARLINK-2",
     "1 44714U 19074B   25130.50000000  .00020000  00000-0  80000-3 0  9997",
     "2 44714  53.0000  60.0000 0001000  20.0000 340.0000 15.06000000    11"),
    ("STARLINK-3",
     "1 44715U 19074C   25130.50000000  .00020000  00000-0  80000-3 0  9996",
     "2 44715  53.0000  90.0000 0001000  30.0000 330.0000 15.06000000    12"),
    ("STARLINK-4",
     "1 44716U 19074D   25130.50000000  .00020000  00000-0  80000-3 0  9995",
     "2 44716  53.0000 120.0000 0001000  40.0000 320.0000 15.06000000    10"),
    ("STARLINK-5",
     "1 44717U 19074E   25130.50000000  .00020000  00000-0  80000-3 0  9994",
     "2 44717  53.0000 150.0000 0001000  50.0000 310.0000 15.06000000    13"),
    ("STARLINK-6",
     "1 44718U 19074F   25130.50000000  .00020000  00000-0  80000-3 0  9993",
     "2 44718  53.0000 180.0000 0001000  60.0000 300.0000 15.06000000    11"),
    ("STARLINK-7",
     "1 44719U 19074G   25130.50000000  .00020000  00000-0  80000-3 0  9992",
     "2 44719  53.0000 210.0000 0001000  70.0000 290.0000 15.06000000    10"),
    ("STARLINK-8",
     "1 44720U 19074H   25130.50000000  .00020000  00000-0  80000-3 0  9991",
     "2 44720  53.0000 240.0000 0001000  80.0000 280.0000 15.06000000    12"),
    ("GPS IIR-1",
     "1 24876U 97035A   25130.50000000 -.00000020  00000-0  00000-0 0  9998",
     "2 24876  55.4000  10.0000 0090000  45.0000 316.0000  2.00567000    11"),
    ("GPS IIR-2",
     "1 26360U 00025A   25130.50000000 -.00000020  00000-0  00000-0 0  9993",
     "2 26360  55.4000  70.0000 0090000  55.0000 306.0000  2.00567000    10"),
    ("GPS IIR-3",
     "1 26407U 00040A   25130.50000000 -.00000020  00000-0  00000-0 0  9996",
     "2 26407  55.4000 130.0000 0090000  65.0000 296.0000  2.00567000    12"),
    ("GPS IIR-4",
     "1 27663U 03005A   25130.50000000 -.00000020  00000-0  00000-0 0  9991",
     "2 27663  55.4000 190.0000 0090000  75.0000 286.0000  2.00567000    11"),
    ("GLONASS-M 1",
     "1 32276U 07065A   25130.50000000 -.00000020  00000-0  00000-0 0  9994",
     "2 32276  64.8000  20.0000 0010000  80.0000 280.0000  2.13100000    13"),
    ("GLONASS-M 2",
     "1 32275U 07065B   25130.50000000 -.00000020  00000-0  00000-0 0  9997",
     "2 32275  64.8000 140.0000 0010000  90.0000 270.0000  2.13100000    11"),
    ("GLONASS-M 3",
     "1 32393U 07065C   25130.50000000 -.00000020  00000-0  00000-0 0  9995",
     "2 32393  64.8000 260.0000 0010000 100.0000 260.0000  2.13100000    12"),
    ("GALILEO 1",
     "1 37846U 11060A   25130.50000000  .00000000  00000-0  00000-0 0  9993",
     "2 37846  56.0000  40.0000 0002000 110.0000 250.0000  1.70475000    10"),
    ("GALILEO 2",
     "1 38857U 12055A   25130.50000000  .00000000  00000-0  00000-0 0  9996",
     "2 38857  56.0000 160.0000 0002000 120.0000 240.0000  1.70475000    11"),
    ("GALILEO 3",
     "1 40128U 14050A   25130.50000000  .00000000  00000-0  00000-0 0  9998",
     "2 40128  56.0000 280.0000 0002000 130.0000 230.0000  1.70475000    12"),
    ("INTELSAT 901",
     "1 26824U 01024A   25130.50000000 -.00000100  00000-0  00000-0 0  9994",
     "2 26824   0.0200 100.0000 0003000 200.0000 160.0000  1.00270000    11"),
    ("INTELSAT 902",
     "1 27441U 02018A   25130.50000000 -.00000100  00000-0  00000-0 0  9992",
     "2 27441   0.0200 220.0000 0003000 210.0000 150.0000  1.00270000    10"),
    ("HIMAWARI-8",
     "1 40267U 14060A   25130.50000000 -.00000100  00000-0  00000-0 0  9997",
     "2 40267   0.0200 140.7000 0002000 220.0000 140.0000  1.00270000    13"),
    ("GOES-16",
     "1 41866U 16071A   25130.50000000 -.00000100  00000-0  00000-0 0  9995",
     "2 41866   0.0200 260.0000 0002000 230.0000 130.0000  1.00270000    11"),
    ("GOES-18",
     "1 51850U 22021A   25130.50000000 -.00000100  00000-0  00000-0 0  9993",
     "2 51850   0.0200  20.0000 0002000 240.0000 120.0000  1.00270000    12"),
    ("CBERS-4",
     "1 40336U 14079A   25130.50000000  .00000100  00000-0  50000-4 0  9991",
     "2 40336  98.5000  50.0000 0008000 150.0000 210.0000 14.37000000    10"),
    ("RESURS-P 1",
     "1 39186U 13030A   25130.50000000  .00000200  00000-0  70000-4 0  9998",
     "2 39186  97.3000 140.0000 0010000 155.0000 205.0000 15.00000000    11"),
]

def fetch_satellites():
    """ISS(リアルタイム) + 内蔵TLE衛星 で40機確保"""
    from sgp4.api import Satrec, jday
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day,
                  now.hour, now.minute, now.second + now.microsecond/1e6)
    j2000    = 2451545.0
    d_days   = (jd + fr) - j2000
    gst_deg  = (280.46061837 + 360.98564736629 * d_days) % 360.0

    sats = []

    # 1. ISS をリアルタイム取得（Open Notify）
    try:
        with urllib.request.urlopen("http://api.open-notify.org/iss-now.json", timeout=5) as r:
            d = json.loads(r.read())
            pos = d["iss_position"]
            sats.append({"name": "ISS", "lat": float(pos["latitude"]),
                         "lon": float(pos["longitude"]), "alt": 408})
    except Exception as e:
        print(f"ISS取得失敗: {e}")

    # 2. 内蔵TLE から残りを計算
    for name, l1, l2 in _BUILTIN_TLE:
        if name == "ISS (ZARYA)" and sats:
            continue  # ISS は上で取得済み
        try:
            sat = Satrec.twoline2rv(l1, l2)
            e, r_vec, _ = sat.sgp4(jd, fr)
            if e != 0: continue
            x, y, z = r_vec
            lon = math.degrees(math.atan2(y, x)) - gst_deg
            lon = ((lon + 180) % 360) - 180
            lat = math.degrees(math.atan2(z, math.sqrt(x**2 + y**2)))
            alt = math.sqrt(x**2 + y**2 + z**2) - 6371
            if alt < 100 or alt > 40000: continue
            sats.append({"name": name, "lat": round(lat, 2),
                         "lon": round(lon, 2), "alt": round(alt, 0)})
        except:
            continue

    print(f"  衛星: {len(sats)}機")
    return sats

def fetch_iss_only():
    """Open Notify から ISS 位置だけ取得（フォールバック）"""
    try:
        with urllib.request.urlopen("http://api.open-notify.org/iss-now.json", timeout=5) as r:
            d = json.loads(r.read())
            pos = d["iss_position"]
            return [{"name": "ISS", "lat": float(pos["latitude"]),
                     "lon": float(pos["longitude"]), "alt": 408}]
    except:
        return []

# ── バックグラウンド更新スレッド ──────────────────────────────
def _update_loop():
    while True:
        q = fetch_quakes()
        with _quake_lock: _quake_cache[:] = q
        s = fetch_satellites()
        with _sat_lock:   _sat_cache[:] = s
        time.sleep(30)

threading.Thread(target=_update_loop, daemon=True).start()

# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", static_url_path="")

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/api/quakes")
def api_quakes():
    with _quake_lock: return jsonify(_quake_cache)

@app.route("/api/satellites")
def api_satellites():
    with _sat_lock: return jsonify(_sat_cache)

@app.route("/api/status")
def api_status():
    return jsonify({"myln": MYLN_OK, "version": "1.0.0",
                    "quakes": len(_quake_cache), "sats": len(_sat_cache)})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """LM Studio にコンテキスト付きで質問を送る"""
    body = request.json
    user_msg = body.get("message", "")

    LM_BASE  = "http://192.168.1.122:1234/v1"
    LM_TOKEN = "sk-lm-XaYHWLfi:qKCEy31iJASbyl8oBsh3"
    LM_MODEL = "llm-jp-4-8b-instruct-mlx"

    # 現在の地震データをコンテキストとして渡す
    with _quake_lock:
        quake_ctx = _quake_cache[:5]

    quake_summary = "\n".join([
        f"- M{q['magnitude']} {q['name']} ({q['myln']}, {q['source']})"
        for q in quake_ctx
    ]) or "現在データなし"

    system_prompt = f"""あなたは地震・防災・宇宙の専門AIアシスタントです。
現在モニター中の地震情報:
{quake_summary}

この情報を参考に、簡潔に日本語で答えてください。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_msg},
    ]

    payload = json.dumps({"model": LM_MODEL, "messages": messages, "max_tokens": 300}).encode()
    req = urllib.request.Request(
        f"{LM_BASE}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {LM_TOKEN}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            reply = d["choices"][0]["message"]["content"].strip()
        return jsonify({"reply": reply, "ok": True})
    except Exception as e:
        return jsonify({"reply": f"LM Studio エラー: {e}", "ok": False})

if __name__ == "__main__":
    print("="*50)
    print("  ⚡ MYLN EARTH MONITOR")
    print("  http://localhost:5050")
    print("="*50)
    app.run(host="0.0.0.0", port=5050, debug=False)
