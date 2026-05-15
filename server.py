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


def fetch_satellites():
    """複数ソースから衛星TLEを取得して位置計算（40機以上を目標）"""
    # 試行するURLリスト（順番に試す）
    # 1. CelesTrak（TLEテキスト形式）
    # 2. SatNOGS API（JSONでTLEを返す、確実に動作）
    tle_sources = [
        ("celestrak_visual",  "https://celestrak.org/GINO/query.php?GROUP=visual&FORMAT=TLE",  "tle_text"),
        ("celestrak_pub",     "https://celestrak.org/pub/TLE/visual.txt",                       "tle_text"),
        ("celestrak_station", "https://celestrak.org/GINO/query.php?GROUP=stations&FORMAT=TLE", "tle_text"),
        ("satnogs",           "https://db.satnogs.org/api/tle/?format=json&page_size=50",       "satnogs_json"),
    ]

    tle_triples = []
    for source_name, url, fmt in tle_sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MYLN-EarthMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace").strip()

            if fmt == "tle_text":
                lines = raw.splitlines()
                # TLE形式の検証: line1が "1 " で始まり、line2が "2 " で始まるブロックが存在するか
                valid = any(
                    lines[ci+1].strip().startswith("1 ") and lines[ci+2].strip().startswith("2 ")
                    for ci in range(len(lines) - 2)
                )
                if not valid:
                    print(f"衛星TLE形式不正: {source_name}")
                    continue
                i = 0
                while i + 2 < len(lines):
                    name = lines[i].strip()
                    l1   = lines[i+1].strip()
                    l2   = lines[i+2].strip()
                    i += 3
                    if l1.startswith("1 ") and l2.startswith("2 "):
                        tle_triples.append((name, l1, l2))
                print(f"衛星TLE取得成功: {source_name} ({len(tle_triples)} entries)")
                break

            elif fmt == "satnogs_json":
                data = json.loads(raw)
                if isinstance(data, list):
                    for item in data:
                        name = item.get("tle0", "").lstrip("0 ").strip()
                        l1   = item.get("tle1", "").strip()
                        l2   = item.get("tle2", "").strip()
                        if l1.startswith("1 ") and l2.startswith("2 "):
                            tle_triples.append((name, l1, l2))
                print(f"衛星TLE取得成功: {source_name} ({len(tle_triples)} entries)")
                break

        except Exception as e:
            print(f"衛星URL失敗 {source_name}: {e}")

    if not tle_triples:
        print("全URLで衛星TLE取得失敗。フォールバックへ")
        return fetch_iss_only()

    try:
        sats = _compute_sats_from_tle_triples(tle_triples)
        print(f"  衛星: {len(sats)}機 計算完了")
        return sats
    except Exception as e:
        print(f"衛星計算エラー: {e}")
        return fetch_iss_only()

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
