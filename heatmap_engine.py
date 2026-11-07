"""Heatmap engine: sector median return over 1 day, 1 week and 1 month, relative to Nifty -> heatmap.json (Yahoo Finance prices)."""
import json, datetime as dt
import numpy as np, pandas as pd, yfinance as yf

NOW = dt.datetime.utcnow()
stocks = json.load(open("stocks.json")).get("stocks", [])
ind = {x["symbol"]: (x.get("industry") or "Unknown") for x in stocks if x.get("industry") and x["industry"] != "Unknown"}
tks = [s + ".NS" for s in ind] + ["^NSEI"]

frames = []
for i in range(0, len(tks), 150):
    try:
        d = yf.download(tks[i:i+150], period="3mo", interval="1d", auto_adjust=True, progress=False, threads=True)
        frames.append(d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]])
    except Exception as e:
        print("price chunk failed:", str(e)[:80])
if not frames or "^NSEI" not in pd.concat(frames, axis=1).columns:
    raise SystemExit("Price data missing. Try again later.")
px = pd.concat(frames, axis=1); px = px.loc[:, ~px.columns.duplicated()]

def ret(c, n):
    c = c.dropna()
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100 if len(c) > n else None

WINDOWS = (("1D", 1), ("1W", 5), ("1M", 21))
nifty = {w: ret(px["^NSEI"], n) for w, n in WINDOWS}

rows = []
for sym, sec in ind.items():
    c = px[sym + ".NS"] if sym + ".NS" in px.columns else None
    if c is None: continue
    r = {w: ret(c, n) for w, n in WINDOWS}
    if any(v is None for v in r.values()): continue
    rows.append({"sector": sec, **r})

out = []
if rows:
    df = pd.DataFrame(rows)
    for sec, g in df.groupby("sector"):
        if len(g) < 5: continue
        item = {"sector": sec, "n": int(len(g))}
        for w, _ in WINDOWS:
            item[w] = round(float(g[w].median()) - nifty[w], 2)
        out.append(item)
out.sort(key=lambda x: -x["1M"])

json.dump({"updated": NOW.isoformat() + "Z",
           "note": "Sector median return minus Nifty return, in percentage points. Price data only.",
           "nifty": {w: round(v, 2) for w, v in nifty.items() if v is not None},
           "sectors": out}, open("heatmap.json", "w"), indent=1)
print("heatmap sectors:", len(out))
