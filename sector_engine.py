"""Sector engine: technical + earnings + quality + policy + cyclical macro -> sectors.json"""
import json, datetime as dt
import numpy as np, pandas as pd, requests, yfinance as yf

st = json.load(open("stocks.json"))
stocks = [x for x in st.get("stocks", []) if x.get("industry") and x["industry"] != "Unknown"]

# Market-wide FII/DII cash flow (NSE). Sector-level flows are not published free, so this is context only.
flows = {"date": None, "fii_net": None, "dii_net": None, "text": "FII/DII cash flow not available today."}
try:
    s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
    s.get("https://www.nseindia.com", timeout=20)
    js = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20).json()
    fii = next((x for x in js if "FII" in str(x.get("category", "")).upper()), None)
    dii = next((x for x in js if "DII" in str(x.get("category", "")).upper() and "FII" not in str(x.get("category", "")).upper()), None)
    if fii and dii:
        fn = float(str(fii.get("netValue")).replace(",", "")); dn = float(str(dii.get("netValue")).replace(",", ""))
        flows = {"date": fii.get("date"), "fii_net": fn, "dii_net": dn,
                 "text": f"Latest market-wide cash flow ({fii.get('date')}): FII net ₹{fn:,.0f} cr, DII net ₹{dn:,.0f} cr (NSE). "
                         "Sector-level flows are not published free, so this is shown as market context, not scored."}
except Exception as e: print("flows fail", e)

# Prices for technicals and macro
tks = [x["symbol"] + ".NS" for x in stocks] + ["BZ=F", "INR=X", "^NSEI"]
px = pd.concat([yf.download(tks[i:i+150], period="1y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
                for i in range(0, len(tks), 150)], axis=1)
px = px.loc[:, ~px.columns.duplicated()]
def chg(c, n): c = c.dropna(); return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
nf1, nf3 = chg(px["^NSEI"], 21), chg(px["^NSEI"], 63)
crude, inr = chg(px["BZ=F"], 63), chg(px["INR=X"], 63)

for x in stocks:
    c = px[x["symbol"] + ".NS"].dropna() if x["symbol"] + ".NS" in px else pd.Series(dtype=float)
    x["rs1"] = (chg(c, 21) - nf1) * 100 if len(c) > 21 and nf1 is not None else None
    x["a200"] = bool(c.iloc[-1] > c.tail(200).mean()) if len(c) >= 200 else None

def med(xs):
    xs = [v for v in xs if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return float(np.median(xs)) if len(xs) >= 3 else None

CRUDE_NEG = ["auto", "chemical", "airline", "consumer", "paint", "transport", "packaging", "tyre"]
CRUDE_POS = ["oil", "gas", "coal", "energy"]
INR_POS = ["software", "it services", "information technology", "pharma", "drug", "healthcare"]
def has(ind, keys): return any(k in ind.lower() for k in keys)

groups = {}
for x in stocks: groups.setdefault(x["industry"], []).append(x)

secs = []
for ind, L in groups.items():
    if len(L) < 5: continue
    rs3 = med([x["rs3"] for x in L]); rs1 = med([x["rs1"] for x in L])
    a200 = [x["a200"] for x in L if x["a200"] is not None]
    pct200 = 100 * np.mean(a200) if len(a200) >= 3 else None
    eg, rg = med([x.get("eg") for x in L]), med([x.get("rg") for x in L])
    roe, de = med([x.get("roe") for x in L]), med([x.get("de") for x in L])
    ups = downs = 0; heads = {}
    for x in L:
        for key in ("UPSIDE", "DOWNSIDE"):
            for it in x.get("reasons", {}).get(key, {}).get("items", []):
                if it["cat"] == "Govt/RBI policy":
                    k = it.get("link") or it["text"]
                    if k in heads: continue
                    heads[k] = {"title": it["text"], "link": it.get("link", ""), "src": it["src"], "dir": it["dir"]}
                    if it["dir"] == "UP": ups += 1
                    else: downs += 1
    cs = -1 if has(ind, CRUDE_NEG) else 1 if has(ind, CRUDE_POS) else 0
    ins = 1 if has(ind, INR_POS) else 0
    secs.append({"name": ind, "n": len(L), "stocks": sorted(L, key=lambda x: -x["momentum"])[:3],
                 "raw": {
                     "tech": (0.5 * rs3 + (0.3 * rs1 if rs1 is not None else 0) + (0.2 * pct200 if pct200 is not None else 0)) if rs3 is not None else None,
                     "earn": (0.5 * eg + 0.5 * rg) if eg is not None and rg is not None else None,
                     "qual": (roe - de / 100) if roe is not None and de is not None else None,
                     "policy": (ups - downs) / max(1, ups + downs),
                     "macro": (cs * crude + ins * inr) if crude is not None and inr is not None else None},
                 "info": {"rs3": rs3, "pct200": pct200, "eg": eg, "rg": rg, "roe": roe, "de": de, "cs": cs, "ins": ins,
                          "ups": ups, "downs": downs},
                 "headlines": list(heads.values())[:4]})

FACT = [("tech", "Technical setup", 35), ("earn", "Earnings growth", 25), ("qual", "Business quality", 10),
        ("policy", "Government / RBI policy", 15), ("macro", "Cyclical macro (crude, INR)", 15)]
def pct_rank(vals, v):
    vs = [a for a in vals if a is not None]
    if v is None or len(vs) < 2: return None
    return 100 * sum(a < v for a in vs) / (len(vs) - 1)

for f, _, _ in FACT:
    vals = [g["raw"][f] for g in secs]
    for g in secs: g.setdefault("pct", {})[f] = pct_rank(vals, g["raw"][f])

def text_of(f, g):
    i = g["info"]
    if f == "tech": return f"Median 3M return {i['rs3']:+.1f}% vs Nifty; {i['pct200']:.0f}% of stocks above 200-day average" if i["pct200"] is not None else f"Median 3M return {i['rs3']:+.1f}% vs Nifty"
    if f == "earn": return f"Median earnings growth {i['eg']*100:+.0f}% and revenue growth {i['rg']*100:+.0f}% across {g['n']} stocks"
    if f == "qual": return f"Median ROE {i['roe']*100:.0f}% and debt/equity {i['de']/100:.2f}" if i["roe"] is not None and i["de"] is not None else "Quality data incomplete"
    if f == "policy": return f"{i['ups']} positive and {i['downs']} negative official headlines matched to this sector" if i["ups"] + i["downs"] else "No matching official policy headlines this period"
    if f == "macro":
        parts = []
        if i["cs"] == -1: parts.append("hurt by rising crude")
        if i["cs"] == 1: parts.append("helped by rising crude")
        if i["ins"]: parts.append("helped by a weaker rupee (exporter)")
        return (f"Brent {crude*100:+.1f}% and USD-INR {inr*100:+.1f}% over 3 months; sector is " + (", ".join(parts) if parts else "not sensitive to these")) if crude is not None and inr is not None else "Macro data incomplete"

out = []
for g in secs:
    fac = []; num = 0; den = 0
    for f, label, w in FACT:
        p = g["pct"][f]
        d = None if p is None else "UP" if p >= 60 else "DOWN" if p <= 40 else "NEUTRAL"
        if p is not None: num += w * p; den += w
        src = {"tech": "Yahoo Finance prices", "earn": "Yahoo Finance fundamentals", "qual": "Yahoo Finance fundamentals",
               "policy": "RBI and PIB releases", "macro": "Yahoo Finance (Brent, USD-INR)"}[f]
        fac.append({"key": f, "label": label, "weight": w, "pct": None if p is None else round(p, 0), "dir": d,
                    "text": text_of(f, g), "src": src})
    g["score"] = round(num / den, 1) if den else 50.0
    g["factors"] = fac
    out.append(g)
out.sort(key=lambda g: -g["score"])
for i, g in enumerate(out):
    g["group"] = "TOP" if i < 3 else "UNDER" if i >= len(out) - 3 else "MID"
result = []
for g in out:
    result.append({"name": g["name"], "n": g["n"], "score": g["score"], "group": g["group"], "tech_rs3": round(g["info"]["rs3"], 1),
                   "factors": g["factors"], "headlines": g["headlines"],
                   "stocks": [{"symbol": x["symbol"], "name": x["name"], "price": x["price"], "rs3": x["rs3"],
                               "momentum": x["momentum"], "bias": x["bias"]} for x in g["stocks"]]})
json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "flows": flows,
           "weights": {l: w for _, l, w in FACT} | {"Flow context (not scored)": 0},
           "sectors": result}, open("sectors.json", "w"), indent=1, default=str)
print("sectors:", len(result))
