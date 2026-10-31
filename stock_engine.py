"""Stock search engine: index membership, F&O list, RSI, 5-day delivery %, momentum, bias, valuation -> stocks.json"""
import io, json, datetime as dt
import numpy as np, pandas as pd, requests, yfinance as yf

H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
IDX = {"Nifty 50": "ind_nifty50list.csv", "Nifty Next 50": "ind_niftynext50list.csv",
       "Nifty 200": "ind_nifty200list.csv", "Nifty 500": "ind_nifty500list.csv"}
s = requests.Session(); s.headers.update(H)
try: s.get("https://www.nseindia.com", timeout=20)
except Exception: pass

def csv(url):
    try: return pd.read_csv(io.StringIO(s.get(url, timeout=30).text), skipinitialspace=True)
    except Exception as e: print("csv fail", url, e); return None

def num(x):
    try:
        v = float(x); return None if np.isnan(v) else v
    except Exception: return None

membership, names = {}, {}
for lab, f in IDX.items():
    d = csv("https://nsearchives.nseindia.com/content/indices/" + f)
    if d is None: continue
    for sym, nm in zip(d["Symbol"], d["Company Name"]):
        membership.setdefault(sym.strip(), []).append(lab); names[sym.strip()] = nm.strip()

fo = csv("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv")
fo_syms = set()
if fo is not None:
    fo.columns = [c.strip() for c in fo.columns]
    fo_syms = {str(x).strip() for x in fo["SYMBOL"].dropna()}

def bhav(day):
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv"
    try: d = pd.read_csv(io.StringIO(s.get(url, timeout=30).text), skipinitialspace=True)
    except Exception: return None
    d.columns = [c.strip() for c in d.columns]
    d = d[d["SERIES"].astype(str).str.strip() == "EQ"]
    d = d.assign(SYMBOL=d["SYMBOL"].astype(str).str.strip()).set_index("SYMBOL")
    return d
days = []; day = dt.date.today()
while len(days) < 5 and (dt.date.today() - day).days < 25:
    if day.weekday() < 5:
        b = bhav(day)
        if b is not None and len(b): days.append((day, b))
    day -= dt.timedelta(days=1)

universe = sorted(set(membership) | fo_syms)
def fetch(tks):
    C, V = {}, {}
    for i in range(0, len(tks), 100):
        ch = tks[i:i+100]
        try: d = yf.download(ch, period="1y", interval="1d", auto_adjust=True, progress=False, threads=True)
        except Exception as e: print(e); continue
        for t in ch:
            try:
                c = d["Close"][t].dropna()
                if len(c) > 120: C[t] = c; V[t] = d["Volume"][t].reindex(c.index)
            except Exception: pass
    return C, V
close, vol = fetch([x + ".NS" for x in universe] + ["^NSEI"])
nf = close.pop("^NSEI")
def ret(c, n): return float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100) if len(c) > n else None
nr1, nr3 = ret(nf, 21), ret(nf, 63)

def rsi(c, n=14):
    d = c.diff().dropna(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False).mean().iloc[-1]; ad = dn.ewm(alpha=1/n, adjust=False).mean().iloc[-1]
    return 100.0 if ad == 0 else float(100 - 100 / (1 + au / ad))

def bias(price, ma50, ma200, rs1, rs3, rs, deliv):
    up, dn, ur, dr = 0, 0, [], []
    if rs3 > 5: up += 25; ur.append(f"Beats Nifty by {rs3:.1f}% over 3 months")
    elif rs3 < -5: dn += 25; dr.append(f"Lags Nifty by {abs(rs3):.1f}% over 3 months")
    if rs1 > 3: up += 15; ur.append(f"Outperforming Nifty last month ({rs1:+.1f}%)")
    elif rs1 < -3: dn += 15; dr.append(f"Underperforming Nifty last month ({rs1:+.1f}%)")
    if price > ma50: up += 15; ur.append("Price above 50-day average")
    else: dn += 15; dr.append("Price below 50-day average")
    if ma200:
        if price > ma200: up += 15; ur.append("Price above 200-day average (long-term uptrend)")
        else: dn += 15; dr.append("Price below 200-day average (long-term downtrend)")
    if rs > 75: dn += 10; dr.append(f"RSI {rs:.0f} overbought, pullback risk")
    elif 50 <= rs <= 70: up += 10; ur.append(f"RSI {rs:.0f} in healthy uptrend zone")
    elif rs < 45: dn += 10; dr.append(f"RSI {rs:.0f} weak")
    d = [x for x in deliv if x is not None]
    if len(d) >= 3:
        avg = sum(d) / len(d)
        if avg >= 50 and d[-1] >= d[0]: up += 15; ur.append(f"Delivery buying rising (avg {avg:.0f}%)")
        elif avg < 35: dn += 10; dr.append(f"Low delivery ({avg:.0f}%), trading is speculative")
        if d[-1] < d[0] - 10: dn += 5; dr.append("Delivery % falling over the week")
    diff = up - dn
    label = "UPSIDE" if diff >= 15 else "DOWNSIDE" if diff <= -15 else "NEUTRAL"
    return label, min(up, 100), min(dn, 100), ur, dr, float(np.clip(50 + diff / 2, 0, 100))

out = []
for sym in universe:
    c = close.get(sym + ".NS")
    if c is None: continue
    try:
        price = float(c.iloc[-1]); r1, r3 = ret(c, 21), ret(c, 63)
        if r1 is None or r3 is None: continue
        ma50 = float(c.tail(50).mean()); ma200 = float(c.tail(200).mean()) if len(c) >= 200 else None
        rs = rsi(c); rs1, rs3 = r1 - nr1, r3 - nr3
        deliv = []
        for dd, b in days:
            pct = num(pd.to_numeric(b.loc[sym]["DELIV_PER"], errors="coerce")) if sym in b.index else None
            deliv.append({"date": dd.strftime("%d %b"), "pct": round(pct, 1) if pct is not None else None})
        deliv = deliv[::-1]
        lab, up, dn, ur, dr, mom = bias(price, ma50, ma200, rs1, rs3, rs, [x["pct"] for x in deliv])
        out.append({"symbol": sym, "name": names.get(sym, sym), "indices": membership.get(sym, []), "fno": sym in fo_syms,
                    "price": round(price, 2), "rsi": round(rs, 1), "deliv": deliv, "momentum": round(mom, 1),
                    "bias": lab, "up": up, "down": dn, "up_reasons": ur, "down_reasons": dr})
    except Exception as e: print(sym, "skip", e)

med = {}
for r in out:
    try:
        i = yf.Ticker(r["symbol"] + ".NS").info
        mc = num(i.get("marketCap"))
        r["mcap_cr"] = round(mc / 1e7) if mc else None
        pe = num(i.get("trailingPE")); ev = num(i.get("enterpriseToEbitda")); pb = num(i.get("priceToBook"))
        eg = num(i.get("earningsGrowth")); ind = i.get("industry") or "Unknown"
        r["industry"] = ind
        r["_v"] = (pe, ev, pb, eg)
        if pe and pe > 0: med.setdefault(ind, {"pe": [], "ev": []})["pe"].append(pe)
        if ev and ev > 0: med.setdefault(ind, {"pe": [], "ev": []})["ev"].append(ev)
    except Exception as e: r["mcap_cr"] = None; r["industry"] = "Unknown"; r["_v"] = (None, None, None, None)
for ind in med:
    med[ind] = {k: float(np.median(v)) for k, v in med[ind].items() if v}
for r in out:
    pe, ev, pb, eg = r.pop("_v"); m = med.get(r["industry"], {}); pts, why = 0, []
    if not pe or pe <= 0:
        r["valuation"] = {"verdict": "NA", "why": ["Loss-making or PE not available"]}; continue
    if m.get("pe"): x = pe / m["pe"]; pts += 1 if x < 0.8 else (-1 if x > 1.25 else 0); why.append(f"PE {pe:.1f} vs sector median {m['pe']:.1f}")
    if ev and m.get("ev"): x = ev / m["ev"]; pts += 1 if x < 0.8 else (-1 if x > 1.25 else 0); why.append(f"EV/EBITDA {ev:.1f} vs sector {m['ev']:.1f}")
    if eg and eg > 0: peg = pe / (eg * 100); pts += 1 if peg < 1 else (-1 if peg > 2 else 0); why.append(f"PEG {peg:.2f}")
    r["valuation"] = {"verdict": "LOW" if pts >= 1.5 else "HIGH" if pts <= -1.5 else "OK", "pe": round(pe, 1), "ev": round(ev, 1) if ev else None, "pb": round(pb, 2) if pb else None, "why": why}

out.sort(key=lambda r: -r["momentum"])
json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "nifty": {"r1": round(nr1, 1), "r3": round(nr3, 1)}, "stocks": out},
          open("stocks.json", "w"), indent=0)
print("stocks:", len(out), "fno:", sum(r["fno"] for r in out))
