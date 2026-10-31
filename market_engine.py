"""Market-edge engine: sectors, valuation, order wins -> data.json (runs on GitHub Actions)."""
import io, re, json, time, datetime as dt
import requests, numpy as np, pandas as pd, yfinance as yf

H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
DRIVERS = {"financial": "Credit growth, interest-rate cycle, asset quality", "capital goods": "Government capex, order books, private investment cycle",
 "information": "US/Europe tech spending, rupee, deal wins", "health": "US generics pricing, domestic demand, approvals",
 "automobile": "Consumer demand, festive season, interest rates, commodity costs", "fast moving": "Rural demand, inflation, raw-material costs",
 "metals": "Global commodity prices, China demand, infra demand", "oil": "Crude price, refining margins, government pricing",
 "power": "Power demand, capacity additions, renewables push", "construction": "Infra spending, housing demand, cement/steel costs",
 "realty": "Interest rates, housing demand", "chemicals": "Global demand, China competition, input costs",
 "consumer": "Discretionary spending, income growth", "telecom": "Tariff hikes, data growth", "services": "Economic activity, trade volumes"}

def driver(ind):
    for k, v in DRIVERS.items():
        if k in ind.lower(): return v
    return "Sector demand and earnings cycle"

def nse():
    s = requests.Session(); s.headers.update(H)
    try: s.get("https://www.nseindia.com", timeout=20)
    except Exception: pass
    return s

def num(x):
    try:
        x = float(x); return None if np.isnan(x) else x
    except Exception: return None

def prices(tickers):
    close, vol = {}, {}
    for i in range(0, len(tickers), 100):
        try:
            d = yf.download(tickers[i:i+100], period="14mo", interval="1d", auto_adjust=True, progress=False, threads=True)
            for t in tickers[i:i+100]:
                try:
                    c = d["Close"][t].dropna()
                    if len(c) > 130: close[t] = c; vol[t] = d["Volume"][t].reindex(c.index)
                except Exception: pass
        except Exception as e: print("download fail", e)
    return close, vol

def rets(c):
    p = c.iloc[-1]; g = lambda n: (p / c.iloc[-1 - n] - 1) * 100 if len(c) > n + 1 else None
    return {"price": round(float(p), 2), "r1": g(21), "r3": g(63), "r6": g(126),
            "ma50": float(c.tail(50).mean()), "ma200": float(c.tail(200).mean()) if len(c) >= 200 else None}

def valuation_of(info):
    return {k: num(info.get(v)) for k, v in dict(price="currentPrice", pe="trailingPE", fpe="forwardPE", pb="priceToBook", ev="enterpriseToEbitda",
            roe="returnOnEquity", eg="earningsGrowth", rg="revenueGrowth", de="debtToEquity", mc="marketCap").items()}

def verdict(r, med, fin):
    if not r["pe"] or r["pe"] <= 0: return "N/A", ["Loss-making or PE not available"]
    pts, why = 0, []
    def rel(val, m, lab):
        nonlocal pts
        if val and m:
            x = val / m; pts += 1 if x < 0.8 else (-1 if x > 1.25 else 0)
            why.append(f"{lab} {val:.1f} vs sector median {m:.1f}")
    rel(r["pe"], med.get("pe"), "PE")
    rel(r["pb"] if fin else r["ev"], med.get("pb" if fin else "ev"), "P/B" if fin else "EV/EBITDA")
    if r["eg"] and r["eg"] > 0:
        peg = r["pe"] / (r["eg"] * 100); r["peg"] = round(peg, 2); pts += 1 if peg < 1 else (-1 if peg > 2 else 0); why.append(f"PEG {peg:.2f}")
    if r["roe"] is not None:
        pts += 0.5 if r["roe"] >= 0.20 else (-0.5 if r["roe"] < 0.08 else 0); why.append(f"ROE {r['roe']*100:.0f}%")
    return ("LOW" if pts >= 1.5 else "HIGH" if pts <= -1.5 else "OK"), why

def orders(s, syms, names):
    out, today = [], dt.date.today()
    for k in range(6):
        to = today - dt.timedelta(15 * k); fr = to - dt.timedelta(15)
        try:
            r = s.get("https://www.nseindia.com/api/corporate-announcements", headers={"Referer": "https://www.nseindia.com/"}, timeout=30,
                      params={"index": "equities", "from_date": fr.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")})
            js = r.json(); items = js if isinstance(js, list) else js.get("data", [])
        except Exception: continue
        for i in items:
            sym, d, t = i.get("symbol"), (i.get("desc") or ""), (i.get("attchmntText") or "")
            if sym not in syms or not any(w in (d + " " + t).lower() for w in ("order", "contract", "award", "letter of")): continue
            m = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|million|mn|billion|bn)", t, re.I)
            val = None
            if m:
                val = float(m.group(1).replace(",", "")) * {"crore": 1, "cr": 1, "million": .1, "mn": .1, "billion": 100, "bn": 100}[m.group(2).lower()]
            out.append({"symbol": sym, "name": names.get(sym, sym), "date": (i.get("an_dt") or "")[:10], "title": d, "text": t, "value_cr": round(val) if val else None, "link": i.get("attchmntFile", "")})
    best = {}
    for o in out:
        if o["symbol"] not in best or (o["value_cr"] or 0) > (best[o["symbol"]]["value_cr"] or 0): best[o["symbol"]] = o
    return sorted(best.values(), key=lambda o: (o["value_cr"] or 0, o["date"]), reverse=True)[:10]

def main():
    s = nse()
    df = pd.read_csv(io.StringIO(s.get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", timeout=30).text))
    names = dict(zip(df["Symbol"], df["Company Name"])); ind = dict(zip(df["Symbol"], df["Industry"]))
    close, vol = prices([x + ".NS" for x in df["Symbol"]] + ["^NSEI"])
    nf = rets(close["^NSEI"]); stocks = {}
    for sym in df["Symbol"]:
        c = close.get(sym + ".NS")
        if c is None: continue
        r = rets(c)
        if None in (r["r1"], r["r3"], r["r6"]): continue
        r["rs1"], r["rs3"], r["rs6"] = r["r1"] - nf["r1"], r["r3"] - nf["r3"], r["r6"] - nf["r6"]
        r["up200"] = bool(r["ma200"] and r["price"] > r["ma200"])
        v = vol[sym + ".NS"].dropna(); r["volx"] = float(v.tail(20).mean() / v.tail(60).mean()) if len(v) > 60 and v.tail(60).mean() else 1
        r["score"] = round(0.4 * r["rs3"] + 0.3 * r["rs1"] + 0.3 * r["rs6"] + (5 if r["up200"] else 0) + (3 if r["price"] > r["ma50"] else 0) + (2 if r["volx"] > 1.2 else 0), 1)
        r.update(symbol=sym, name=names[sym], industry=ind[sym]); stocks[sym] = r
    secs = []
    for name, g in pd.DataFrame(stocks.values()).groupby("industry"):
        if len(g) < 5: continue
        m = g[["r1", "r3", "r6"]].median(); br = g["up200"].mean() * 100
        sc = 0.4 * (m.r3 - nf["r3"]) + 0.3 * (m.r1 - nf["r1"]) + 0.3 * (m.r6 - nf["r6"]) + 0.1 * (br - 50)
        improving = (m.r1 - nf["r1"]) > (m.r3 - nf["r3"]) / 3
        top = g.sort_values("score", ascending=False).head(3)
        secs.append({"name": name, "score": round(sc, 1), "n": len(g), "improving": bool(improving), "driver": driver(name),
            "reasons": [f"3M {m.r3:+.1f}% vs Nifty {nf['r3']:+.1f}%", f"1M {m.r1:+.1f}% vs Nifty {nf['r1']:+.1f}%", f"{br:.0f}% of stocks above 200-day average",
                        "Momentum improving in the last month" if improving else "Momentum slowing in the last month"],
            "stocks": [{k: (round(x[k], 1) if isinstance(x[k], float) else x[k]) for k in ("symbol", "name", "price", "r1", "r3", "rs3", "score")} for _, x in top.iterrows()]})
    secs.sort(key=lambda x: -x["score"])
    emerging = [x["name"] for x in sorted(secs[3:], key=lambda x: -x["score"]) if x["improving"]][:2]
    val = {}
    for sym in stocks:
        try: val[sym] = valuation_of(yf.Ticker(sym + ".NS").info)
        except Exception: pass
        time.sleep(0.2)
    med = {}
    for k in ("pe", "ev", "pb"):
        for i, g in pd.DataFrame([{"ind": ind[x], k: v[k]} for x, v in val.items() if v.get(k) and v[k] > 0]).groupby("ind"): med.setdefault(i, {})[k] = float(g[k].median())
    vl = []
    for sym, v in val.items():
        lab, why = verdict(v, med.get(ind[sym], {}), "financial" in ind[sym].lower())
        vl.append({"symbol": sym, "name": names[sym], "industry": ind[sym], **{k: (round(x, 2) if x is not None else None) for k, x in v.items() if k != "mc"},
                   "mcap_cr": round(v["mc"] / 1e7) if v.get("mc") else None, "sector_med": {k: round(x, 1) for k, x in med.get(ind[sym], {}).items()}, "verdict": lab, "why": why})
    data = {"updated": dt.datetime.utcnow().isoformat() + "Z", "nifty": {"r1": round(nf["r1"], 1), "r3": round(nf["r3"], 1), "up200": bool(nf["ma200"] and nf["price"] > nf["ma200"])},
            "sectors": {"top": secs[:3], "under": secs[-3:][::-1], "emerging": emerging}, "valuation": vl, "orders": orders(s, set(df["Symbol"]), names)}
    json.dump(data, open("data.json", "w"), indent=0)

main()
