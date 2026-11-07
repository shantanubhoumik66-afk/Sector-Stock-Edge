"""Power engine. Reads the outputs the other engines already write and adds:
1. Shared master list (master.json)
2. Data health check and last-good backup (health.json, last_good/)
3. Power score: momentum ranked within sector + results + valuation + bias (power.json)
4. Daily audit snapshot for back-testing (audit/YYYY-MM-DD.json)
Run it after the other daily engines."""
import json, os, shutil, datetime as dt
import numpy as np, pandas as pd

NOW = dt.datetime.utcnow()

def load(path, default):
    try: return json.load(open(path))
    except Exception: return default

stocks = load("stocks.json", {}).get("stocks", [])
results = load("results.json", {}).get("present", [])
track = load("track_record.json", {})

# 1. Data health and last-good backup
FILES = {"stocks.json": 36, "sectors.json": 36, "results.json": 72, "data.json": 36,
         "option.json": 2, "track_record.json": 800, "backtest.json": 800}
health = {}
for f, max_h in FILES.items():
    d = load(f, None)
    if d is None:
        health[f] = {"status": "MISSING", "updated": None, "age_hours": None}; continue
    upd = d.get("updated")
    age = None
    try:
        if upd: age = round((NOW - dt.datetime.fromisoformat(upd.replace("Z", ""))).total_seconds() / 3600, 1)
    except Exception: pass
    status = "MISSING" if not upd else ("STALE" if age is not None and age > max_h else "OK")
    health[f] = {"status": status, "updated": upd, "age_hours": age}
health["stocks.json"]["records"] = len(stocks)
health["stocks.json"]["bad_records"] = sum(1 for x in stocks if x.get("price") is None or x.get("rsi") is None or x.get("momentum") is None)
health["results.json"]["records"] = len(results)
os.makedirs("last_good", exist_ok=True)
for f, h in health.items():
    if h["status"] == "OK" and os.path.exists(f): shutil.copy(f, f"last_good/{f}")
ok = sum(1 for v in health.values() if v["status"] == "OK")
json.dump({"updated": NOW.isoformat() + "Z", "summary": f"{ok}/{len(health)} data files OK", "files": health},
          open("health.json", "w"), indent=1, default=str)

# 2. Master list (one shared universe)
master = [{"symbol": x["symbol"], "name": x.get("name", x["symbol"]), "fno": bool(x.get("fno")),
           "indices": x.get("indices", []), "sector": x.get("industry"), "mcap_cr": x.get("mcap_cr")} for x in stocks]
json.dump({"updated": NOW.isoformat() + "Z", "count": len(master), "stocks": master},
          open("master.json", "w"), indent=0, default=str)

# 3. Power score (0 to 100)
res_map = {r["symbol"]: r.get("verdict") for r in results}
df = pd.DataFrame([{"symbol": x["symbol"], "name": x.get("name", x["symbol"]), "sector": x.get("industry") or "Unknown",
                    "mom": x.get("momentum"), "rsi": x.get("rsi"), "fno": bool(x.get("fno")), "bias": x.get("bias"),
                    "val": (x.get("valuation") or {}).get("verdict"), "price": x.get("price")} for x in stocks])
df = df.dropna(subset=["mom"]).reset_index(drop=True)
if len(df):
    cnt = df.groupby("sector")["mom"].transform("count")
    glob = df["mom"].rank(pct=True) * 100
    sect = df.groupby("sector")["mom"].rank(pct=True) * 100
    df["mom_pct"] = np.where(cnt >= 5, sect, glob)  # rank within sector, global rank for small sectors

rows = []
for _, r in df.iterrows():
    rv = res_map.get(r["symbol"])
    rp = {"GOOD": 20, "NEUTRAL": 10, "VERY BAD": 0}.get(rv, 10)
    vp = {"LOW": 15, "OK": 8, "HIGH": 0}.get(r["val"], 8)
    bp = {"UPSIDE": 15, "NEUTRAL": 8, "DOWNSIDE": 0}.get(r["bias"], 8)
    overbought = (r["rsi"] or 0) > 75
    score = round(max(0, min(100, 0.5 * r["mom_pct"] + rp + vp + bp - (5 if overbought else 0))), 1)
    why = [f"Momentum in top {max(1, round(100 - r['mom_pct']))}% of {r['sector']}",
           f"Latest results: {rv}" if rv else "No recent results verdict",
           f"Valuation: {r['val'] or 'not available'}",
           f"Bias: {r['bias']}"]
    if overbought: why.append("RSI above 75: overbought, pullback risk")
    rows.append({"symbol": r["symbol"], "name": r["name"], "sector": r["sector"], "fno": r["fno"], "score": score,
                 "bias": r["bias"], "val": r["val"], "results": rv, "why": why})
rows.sort(key=lambda x: -x["score"])

by_sector = []
if rows:
    for sec, grp in pd.DataFrame(rows).groupby("sector"):
        if len(grp) < 5: continue
        top3 = grp.sort_values("score", ascending=False).head(3)
        by_sector.append({"sector": sec, "top": top3.to_dict("records")})
    by_sector.sort(key=lambda s: -s["top"][0]["score"])

json.dump({"updated": NOW.isoformat() + "Z",
           "note": "Power score = momentum ranked within sector (up to 50) + latest results (up to 20) + valuation (up to 15) + bias (up to 15), minus 5 if overbought. Rule-based, not advice.",
           "top": rows[:20], "by_sector": by_sector[:10], "track": track.get("summary", [])},
          open("power.json", "w"), indent=1, default=str)

# 4. Daily audit snapshot (never rewritten during the same day after this run)
os.makedirs("audit", exist_ok=True)
day = NOW.strftime("%Y-%m-%d")
audit = {"date": day, "generated": NOW.isoformat() + "Z",
         "rows": [{"symbol": r["symbol"], "score": r["score"], "bias": r["bias"], "results": r["results"], "valuation": r["val"]} for r in rows]}
json.dump(audit, open(f"audit/{day}.json", "w"), default=str)

print("health:", health and json.dumps({"ok": ok, "total": len(health)}))
print("power stocks:", len(rows), "top:", rows[0]["symbol"] if rows else "-")
