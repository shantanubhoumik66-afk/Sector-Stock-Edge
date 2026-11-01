"""Sector engine: technical + earnings + quality + policy + cyclical macro -> sectors.json (robust to missing data)"""
import json, datetime as dt, traceback
import numpy as np, pandas as pd, requests, yfinance as yf

def fmt(v, spec="+.1f", mult=1, suffix=""):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)): return "–"
        return format(float(v) * mult, spec) + suffix
    except Exception: return "–"

def med(xs):
    xs = [float(v) for v in xs if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return float(np.median(xs)) if len(xs) >= 3 else None

def write(obj):
    json.dump(obj, open("sectors.json", "w"), indent=1, default=str)

try:
    st = json.load(open("stocks.json"))
    stocks = [x for x in st.get("stocks", []) if x.get("industry") and x["industry"] != "Unknown"]
    if len(stocks) < 20: raise SystemExit("stocks.json has too few stocks. Run 'Update stock search' first.")

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
    except Exception as e: print("flows skipped:", e)

    # Prices
    tks = [x["symbol"] + ".NS" for x in stocks] + ["BZ=F", "INR=X", "^NSEI"]
    frames = []
    for i in range(0, len(tks), 150):
        try:
            d = yf.download(tks[i:i+150], period="1y", interval="1d", auto_adjust=True, progress=False, threads=True)
            if isinstance(d.columns, pd.MultiIndex): d = d["Close"]
            else: d = d[["Close"]]
            frames.append(d)
        except Exception as e: print("price chunk failed:", e)
    if not frames: raise SystemExit("Price download failed (Yahoo may be rate-limiting). Try again later.")
    px = pd.concat(frames, axis=1); px = px.loc[:, ~px.columns.duplicated()]
    if "^NSEI" not in px.columns: raise SystemExit("Nifty price data missing. Try again later.")

    def chg(c, n):
        c = c.dropna() if c is not None else pd.Series(dtype=float)
        return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
    nf1, nf3 = chg(px["^NSEI"], 21), chg(px["^NSEI"], 63)
    crude = chg(px["BZ=F"], 63) if "BZ=F" in px.columns else None
    inr = chg(px["INR=X"], 63) if "INR=X" in px.columns else None

    for x in stocks:
        c = px[x["symbol"] + ".NS"].dropna() if x["symbol"] + ".NS" in px.columns else pd.Series(dtype=float)
        r1 = chg(c, 21)
        x["rs1"] = (r1 - nf1) * 100 if r1 is not None and nf1 is not None else None
        x["a200"] = bool(c.iloc[-1] > c.tail(200).mean()) if len(c) >= 200 else None

    CRUDE_NEG = ["auto", "chemical", "airline", "consumer", "paint", "transport", "packaging", "tyre"]
    CRUDE_POS = ["oil", "gas", "coal", "energy"]
    INR_POS = ["software", "it services", "information technology", "pharma", "drug", "healthcare"]
    has = lambda ind, keys: any(k in ind.lower() for k in keys)

    groups = {}
    for x in stocks: groups.setdefault(x["industry"], []).append(x)

    secs = []
    for ind, L in groups.items():
        if len(L) < 5: continue
        rs3 = med([x.get("rs3") for x in L]); rs1 = med([x.get("rs1") for x in L])
        a200 = [x["a200"] for x in L if x.get("a200") is not None]
        pct200 = 100 * float(np.mean(a200)) if len(a200) >= 3 else None
        eg, rg = med([x.get("eg") for x in L]), med([x.get("rg") for x in L])
        roe, de = med([x.get("roe") for x in L]), med([x.get("de") for x in L])
        ups = downs = 0; heads = {}
        for x in L:
            for key in ("UPSIDE", "DOWNSIDE"):
                for it in (x.get("reasons") or {}).get(key, {}).get("items", []):
                    if it.get("cat") != "Govt/RBI policy": continue
                    k = it.get("link") or it["text"]
                    if k in heads: continue
                    heads[k] = {"title": it["text"], "link": it.get("link", ""), "src": it.get("src", ""), "dir": it.get("dir")}
                    if it.get("dir") == "UP": ups += 1
                    else: downs += 1
        cs = -1 if has(ind, CRUDE_NEG) else 1 if has(ind, CRUDE_POS) else 0
        ins = 1 if has(ind, INR_POS) else 0
        raw = {
            "tech": (0.5 * rs3 + (0.3 * rs1 if rs1 is not None else 0) + (0.2 * pct200 if pct200 is not None else 0)) if rs3 is not None else None,
            "earn": (0.5 * eg + 0.5 * rg) if eg is not None and rg is not None else None,
            "qual": (roe - de / 100) if roe is not None and de is not None else None,
            "policy": (ups - downs) / max(1, ups + downs),
            "macro": (cs * crude + ins * inr) if crude is not None and inr is not None else None}
        secs.append({"name": ind, "n": len(L), "raw": raw, "rs3": rs3, "pct200": pct200, "eg": eg, "rg": rg, "roe": roe, "de": de,
                     "cs": cs, "ins": ins, "ups": ups, "downs": downs,
                     "stocks": sorted(L, key=lambda x: -(x.get("momentum") or 0))[:3], "headlines": list(heads.values())[:4]})
    if not secs: raise SystemExit("No sector has at least 5 stocks. Check stocks.json.")

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
        if f == "tech":
            t = f"Median 3M return {fmt(g['rs3'])}% vs Nifty"
            return t + (f"; {fmt(g['pct200'], '.0f')}% of stocks above 200-day average" if g["pct200"] is not None else "")
        if f == "earn": return f"Median earnings growth {fmt(g['eg'], '+.0f', 100)}% and revenue growth {fmt(g['rg'], '+.0f', 100)}% across {g['n']} stocks"
        if f == "qual": return f"Median ROE {fmt(g['roe'], '.0f', 100)}% and debt/equity {fmt(g['de'], '.2f', 0.01)}"
        if f == "policy":
            return (f"{g['ups']} positive and {g['downs']} negative official headlines matched to this sector"
                    if g["ups"] + g["downs"] else "No matching official policy headlines this period")
        if f == "macro":
            if crude is None or inr is None: return "Macro price data unavailable today"
            parts = []
            if g["cs"] == -1: parts.append("hurt by rising crude")
            if g["cs"] == 1: parts.append("helped by rising crude")
            if g["ins"]: parts.append("helped by a weaker rupee (exporter)")
            return f"Brent {fmt(crude, '+.1f', 100)}% and USD-INR {fmt(inr, '+.1f', 100)}% over 3 months; sector is " + (", ".join(parts) if parts else "not sensitive to these")
        return ""
    SRC = {"tech": "Yahoo Finance prices", "earn": "Yahoo Finance fundamentals", "qual": "Yahoo Finance fundamentals",
           "policy": "RBI and PIB releases", "macro": "Yahoo Finance (Brent, USD-INR)"}

    out = []
    for g in secs:
        fac = []; num = 0.0; den = 0
        for f, label, w in FACT:
            p = g["pct"][f]
            d = None if p is None else "UP" if p >= 60 else "DOWN" if p <= 40 else "NEUTRAL"
            if p is not None: num += w * p; den += w
            fac.append({"key": f, "label": label, "weight": w, "pct": None if p is None else round(p), "dir": d,
                        "text": text_of(f, g), "src": SRC[f]})
        g["score"] = round(num / den, 1) if den else 50.0
        g["factors"] = fac; out.append(g)
    out.sort(key=lambda g: -g["score"])
    result = []
    for i, g in enumerate(out):
        grp = "TOP" if i < 3 else "UNDER" if i >= len(out) - 3 else "MID"
        result.append({"name": g["name"], "n": g["n"], "score": g["score"], "group": grp,
                       "tech_rs3": round(g["rs3"], 1) if g["rs3"] is not None else None,
                       "factors": g["factors"], "headlines": g["headlines"],
                       "stocks": [{"symbol": x["symbol"], "name": x.get("name", ""), "price": x.get("price"), "rs3": x.get("rs3"),
                                   "momentum": x.get("momentum"), "bias": x.get("bias")} for x in g["stocks"]]})
    weights = {l: w for _, l, w in FACT}; weights["Flow context (not scored)"] = 0
    write({"updated": dt.datetime.utcnow().isoformat() + "Z", "flows": flows, "weights": weights, "sectors": result})
    print("sectors:", len(result))
except SystemExit as e:
    print("STOPPED:", e); raise
except Exception:
    traceback.print_exc(); raise
           
