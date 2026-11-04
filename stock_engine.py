"""Stock engine: index/F&O lists, RSI, delivery, up to 10 sourced reasons per direction, confidence -> stocks.json"""
import io, re, json, datetime as dt
import xml.etree.ElementTree as ET
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
    return d.assign(SYMBOL=d["SYMBOL"].astype(str).str.strip()).set_index("SYMBOL")
days = []; day = dt.date.today()
while len(days) < 5 and (dt.date.today() - day).days < 25:
    if day.weekday() < 5:
        b = bhav(day)
        if b is not None and len(b): days.append((day, b))
    day -= dt.timedelta(days=1)

# Genuine exchange announcements (last ~45 days)
ann = {}
today = dt.date.today()
for k in range(3):
    to = today - dt.timedelta(15 * k); fr = to - dt.timedelta(15)
    try:
        js = s.get("https://www.nseindia.com/api/corporate-announcements", headers={"Referer": "https://www.nseindia.com/"},
                   timeout=30, params={"index": "equities", "from_date": fr.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")}).json()
        items = js if isinstance(js, list) else js.get("data", [])
    except Exception as e:
        print("announcements fail", e); items = []
    for i in items:
        sym = str(i.get("symbol", "")).strip()
        ann.setdefault(sym, []).append({"date": str(i.get("an_dt") or "")[:10], "desc": str(i.get("desc") or ""),
                                        "text": str(i.get("attchmntText") or ""), "link": str(i.get("attchmntFile") or "")})

# Official policy headlines (RBI and PIB RSS)
heads = []
for url, src in [("https://www.rbi.org.in/pressreleases_rss.xml", "RBI press release"),
                 ("https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "PIB (Government of India)")]:
    try:
        root = ET.fromstring(s.get(url, timeout=30).content)
        for it in root.iter("item"):
            heads.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                          "src": src, "date": (it.findtext("pubDate") or "")[:16]})
    except Exception as e: print("feed fail", url, e)

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
    if rs3 > 5: up += 25; ur.append(f"Beats Nifty by {rs3:.1f}% over 3 months (price data)")
    elif rs3 < -5: dn += 25; dr.append(f"Lags Nifty by {abs(rs3):.1f}% over 3 months (price data)")
    if rs1 > 3: up += 15; ur.append(f"Outperforming Nifty last month, {rs1:+.1f}% (price data)")
    elif rs1 < -3: dn += 15; dr.append(f"Underperforming Nifty last month, {rs1:+.1f}% (price data)")
    if price > ma50: up += 15; ur.append("Price above 50-day average (price data)")
    else: dn += 15; dr.append("Price below 50-day average (price data)")
    if ma200:
        if price > ma200: up += 15; ur.append("Price above 200-day average, long-term uptrend (price data)")
        else: dn += 15; dr.append("Price below 200-day average, long-term downtrend (price data)")
    if rs > 75: dn += 10; dr.append(f"RSI {rs:.0f} overbought, pullback risk (price data)")
    elif 50 <= rs <= 70: up += 10; ur.append(f"RSI {rs:.0f} in healthy uptrend zone (price data)")
    elif rs < 45: dn += 10; dr.append(f"RSI {rs:.0f} weak (price data)")
    d = [x for x in deliv if x is not None]
    if len(d) >= 3:
        avg = sum(d) / len(d)
        if avg >= 50 and d[-1] >= d[0]: up += 15; ur.append(f"Delivery buying rising, average {avg:.0f}% (NSE bhavcopy)")
        elif avg < 35: dn += 10; dr.append(f"Low delivery, average {avg:.0f}%, trading is speculative (NSE bhavcopy)")
        if d[-1] < d[0] - 10: dn += 5; dr.append("Delivery % falling over the week (NSE bhavcopy)")
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
                    "price": round(price, 2), "rsi": round(rs, 1), "rs3": round(rs3, 1), "deliv": deliv, "momentum": round(mom, 1),
                    "bias": lab, "up": up, "down": dn, "up_reasons": ur, "down_reasons": dr, "_ur": ur, "_dr": dr})
    except Exception as e: print(sym, "skip", e)

# Valuation and fundamentals from Yahoo Finance
med = {}
for r in out:
    try:
        i = yf.Ticker(r["symbol"] + ".NS").info
        mc = num(i.get("marketCap"))
        r["mcap_cr"] = round(mc / 1e7) if mc else None
        pe = num(i.get("trailingPE")); ev = num(i.get("enterpriseToEbitda")); pb = num(i.get("priceToBook"))
        eg = num(i.get("earningsGrowth")); ind = i.get("industry") or "Unknown"
        r["industry"] = ind; r["eg"] = eg; r["rg"] = num(i.get("revenueGrowth")); r["roe"] = num(i.get("returnOnEquity")); r["de"] = num(i.get("debtToEquity"))
        r["_v"] = (pe, ev, pb)
        if pe and pe > 0: med.setdefault(ind, {"pe": [], "ev": []})["pe"].append(pe)
        if ev and ev > 0: med.setdefault(ind, {"pe": [], "ev": []})["ev"].append(ev)
    except Exception:
        r["mcap_cr"] = None; r["industry"] = "Unknown"; r["eg"] = None; r["rg"] = None; r["roe"] = None; r["de"] = None; r["_v"] = (None, None, None)
for ind in med: med[ind] = {k: float(np.median(v)) for k, v in med[ind].items() if v}

# Sector demand proxy: median 3M relative strength per industry (from price data)
sector_rs = {}
for r in out: sector_rs.setdefault(r["industry"], []).append(r["rs3"])
sector_rs = {k: float(np.median(v)) for k, v in sector_rs.items() if len(v) >= 5}

KEYS = {"bank": ["bank", "credit", "rbi", "loan", "interest rate"], "financial": ["nbfc", "credit", "rbi", "loan", "interest rate"],
        "auto": ["auto", "vehicle", "electric vehicle", "gst"], "steel": ["steel", "metal", "mining", "import duty"],
        "oil": ["oil", "crude", "petroleum", "fuel"], "power": ["power", "electricity", "renewable", "solar"],
        "pharma": ["drug", "pharma", "medicine", "healthcare"], "software": ["software", "it services", "digital"],
        "construction": ["infrastructure", "road", "railway", "housing", "cement"], "consumer": ["consumer", "fmcg", "rural", "food"],
        "telecom": ["telecom", "spectrum"], "chemical": ["chemical"], "real estate": ["housing", "real estate", "property"]}
def keys_for(ind):
    l = ind.lower(); o = []
    for k, v in KEYS.items():
        if k in l: o += v
    return o
UPW = ["cut", "relief", "incentive", "approves", "approval", "boost", "support", "reduces", "subsid", "scheme", "reform"]
DNW = ["hike", "ban", "curb", "penalty", "tighten", "raises", "restrict", "duty", "tax", "levy"]
PRIO = ["Order book", "Corporate action", "Governance/regulatory", "Results", "Govt/RBI policy",
        "Sector demand", "Valuation", "Relative strength", "Delivery", "Trend/technical"]

def cat_of(t):
    if "elivery" in t: return "Delivery"
    if "Nifty" in t: return "Relative strength"
    return "Trend/technical"

for r in out:
    pe, ev, pb = r.pop("_v"); m = med.get(r["industry"], {}); R = []
    def add(cat, d, text, src, link="", date=""): R.append({"cat": cat, "dir": d, "text": text[:260], "src": src, "link": link, "date": date})
    # Exchange filings
    for a in ann.get(r["symbol"], [])[:20]:
        low = (a["desc"] + " " + a["text"]).lower(); body = a["text"] or a["desc"]
        if re.search(r"order|contract|award|letter of", low): add("Order book", "UP", f"{a['desc']}: {body}", "NSE corporate filing", a["link"], a["date"])
        elif re.search(r"buyback|bonus|dividend|split", low): add("Corporate action", "UP", f"{a['desc']}: {body}", "NSE corporate filing", a["link"], a["date"])
        elif re.search(r"penalty|fraud|resign|default|downgrade|pledge|insolven|show cause|sebi order", low) and names.get(r["symbol"], r["symbol"]).split()[0].lower() in low: add("Governance/regulatory", "DOWN", f"{a['desc']}: {body}", "NSE corporate filing", a["link"], a["date"])
        elif re.search(r"financial result|quarterly result|results", low) and r["eg"] is not None and abs(r["eg"]) >= 0.1:
            add("Results", "UP" if r["eg"] > 0 else "DOWN", f"Results filed. Latest earnings growth {r['eg']*100:+.0f}%", "NSE filing + Yahoo Finance", a["link"], a["date"])
    # Policy headlines matched to industry
    kw = keys_for(r["industry"])
    for h in heads:
        t = h["title"].lower()
        if kw and any(k in t for k in kw):
            d = "UP" if any(w in t for w in UPW) else "DOWN" if any(w in t for w in DNW) else None
            if d: add("Govt/RBI policy", d, h["title"], h["src"], h["link"], h["date"])
    # Sector demand proxy
    sr = sector_rs.get(r["industry"])
    if sr is not None and abs(sr) >= 2:
        add("Sector demand", "UP" if sr > 0 else "DOWN", f"{r['industry']} sector median 3M return is {sr:+.1f}% vs Nifty (price data)", "Yahoo Finance prices")
    # Valuation
    if pe and pe > 0 and m.get("pe"):
        x = pe / m["pe"]; v = "LOW" if x < 0.8 else "HIGH" if x > 1.25 else "OK"
        if v != "OK": add("Valuation", "UP" if v == "LOW" else "DOWN", f"PE {pe:.1f} vs sector median {m['pe']:.1f}: {v.lower()} valuation", "Yahoo Finance")
    # Price, delivery and technical reasons
    for t in r.pop("_ur"): add(cat_of(t), "UP", t, "NSE bhavcopy" if "elivery" in t else "Yahoo Finance prices")
    for t in r.pop("_dr"): add(cat_of(t), "DOWN", t, "NSE bhavcopy" if "elivery" in t else "Yahoo Finance prices")
    R.sort(key=lambda x: PRIO.index(x["cat"]) if x["cat"] in PRIO else 99)
    res = {}
    for d, key in (("UP", "UPSIDE"), ("DOWN", "DOWNSIDE")):
        items, cnt = [], {}
        for x in R:
            if x["dir"] != d: continue
            if cnt.get(x["cat"], 0) >= 3: continue
            cnt[x["cat"]] = cnt.get(x["cat"], 0) + 1; items.append(x)
        items = items[:10]; n = len(items); c = len({x["cat"] for x in items})
        conf = "HIGH" if n >= 7 and c >= 4 else "MEDIUM" if n >= 4 and c >= 3 else "LOW"
        res[key] = {"items": items, "count": n, "cats": c, "confidence": conf}
    r["reasons"] = res
    r["up_reasons"] = [x["text"] for x in res["UPSIDE"]["items"][:3]]
    r["down_reasons"] = [x["text"] for x in res["DOWNSIDE"]["items"][:3]]
    r["valuation"] = {"verdict": "NA", "why": []}
    if pe and pe > 0:
        pts, why = 0, []
        if m.get("pe"): x = pe / m["pe"]; pts += 1 if x < 0.8 else (-1 if x > 1.25 else 0); why.append(f"PE {pe:.1f} vs sector median {m['pe']:.1f}")
        if ev and m.get("ev"): x = ev / m["ev"]; pts += 1 if x < 0.8 else (-1 if x > 1.25 else 0); why.append(f"EV/EBITDA {ev:.1f} vs sector {m['ev']:.1f}")
        if r["eg"] and r["eg"] > 0: peg = pe / (r["eg"] * 100); pts += 1 if peg < 1 else (-1 if peg > 2 else 0); why.append(f"PEG {peg:.2f}")
        r["valuation"] = {"verdict": "LOW" if pts >= 1.5 else "HIGH" if pts <= -1.5 else "OK", "pe": round(pe, 1),
                          "ev": round(ev, 1) if ev else None, "pb": round(pb, 2) if pb else None, "why": why}
    pass

out.sort(key=lambda r: -r["momentum"])
json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "nifty": {"r1": round(nr1, 1), "r3": round(nr3, 1)}, "stocks": out},
          open("stocks.json", "w"), indent=0, default=str)
print("stocks:", len(out), "fno:", sum(r["fno"] for r in out), "headlines:", len(heads), "announcements:", sum(len(v) for v in ann.values()))
