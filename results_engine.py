"""Companies results engine: latest quarter, verdict with score breakdown, 4-quarter history, result dates -> results.json (Yahoo Finance)."""
import json, datetime as dt
import numpy as np, pandas as pd, yfinance as yf

NOW = pd.Timestamp.now()
st = json.load(open("stocks.json")).get("stocks", [])
def nse_list(url):
    try:
        import io, requests
        s_ = requests.Session(); s_.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
        s_.get("https://www.nseindia.com", timeout=20)
        d = pd.read_csv(io.StringIO(s_.get(url, timeout=30).text), skipinitialspace=True)
        d.columns = [c.strip() for c in d.columns]
        return d
    except Exception as e:
        print("NSE list failed:", str(e)[:80]); return None

n500 = nse_list("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv")
fo_df = nse_list("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv")
fo_set = {str(x).strip() for x in fo_df["SYMBOL"].dropna()} if fo_df is not None else set()
names_map = dict(zip(n500["Symbol"].str.strip(), n500["Company Name"].str.strip())) if n500 is not None else {}
symbols = set(names_map) | fo_set
if not symbols:
    symbols = {x["symbol"] for x in st}
universe = [{"symbol": sy, "name": names_map.get(sy, sy), "fno": sy in fo_set} for sy in sorted(symbols)]
print("companies to check:", len(universe), "F&O:", len(fo_set))
REV = ["Total Revenue", "Operating Revenue"]
PAT = ["Net Income", "Net Income Common Stockholders"]
EBT = ["EBITDA", "Normalized EBITDA"]

def num(x):
    try:
        v = float(x); return None if np.isnan(v) else v
    except Exception: return None

def cr(v): return None if v is None else round(v / 1e7, 1)

def pick(df, names, col):
    if df is None or getattr(df, "empty", True): return None
    for n in names:
        if n in df.index:
            row = df.loc[n]
            if isinstance(row, pd.DataFrame): row = row.iloc[0]
            if col < len(row): return num(row.iloc[col])
    return None

def g(a, b):
    return None if a is None or not b else round((a - b) / abs(b) * 100, 1)

def gscore(v, w):
    if v is None: return 0
    if v >= 10: return 2 * w
    if v >= 0: return 1 * w
    if v > -10: return -1 * w
    return -2 * w

present, upcoming, errors = [], [], 0
for x in universe:
    sym = x["symbol"]; name = x.get("name", sym)
    try:
        t = yf.Ticker(sym + ".NS")
        try:
            mc = t.fast_info["market_cap"]
            if mc and mc / 1e7 < 10000: continue
        except Exception: pass
        inc, bs, cf = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow
        if inc is None or inc.empty or inc.shape[1] < 2: continue
        qend = pd.Timestamp(inc.columns[0])
        if qend.tzinfo: qend = qend.tz_localize(None)

        reported = nxt = None
        try:
            ed = t.get_earnings_dates(limit=12)
            if ed is not None and len(ed):
                ds = [pd.Timestamp(i) for i in ed.index]
                ds = [d.tz_localize(None) if d.tzinfo else d for d in ds]
                past = [d for d in ds if d <= NOW]; fut = [d for d in ds if d > NOW]
                reported = max(past).date().isoformat() if past else None
                nxt = min(fut).date().isoformat() if fut else None
        except Exception: pass
        if nxt: upcoming.append({"symbol": sym, "name": name, "next_date": nxt})
        if (NOW - qend).days > 120: continue

        rev, rev1, rev4 = pick(inc, REV, 0), pick(inc, REV, 1), pick(inc, REV, 4)
        pat, pat1, pat4 = pick(inc, PAT, 0), pick(inc, PAT, 1), pick(inc, PAT, 4)
        ebitda, ebitda4 = pick(inc, EBT, 0), pick(inc, EBT, 4)
        eps = pick(inc, ["Diluted EPS", "Basic EPS"], 0)
        opm = round(ebitda / rev * 100, 1) if ebitda is not None and rev else None
        opm4 = round(ebitda4 / rev4 * 100, 1) if ebitda4 is not None and rev4 else None
        debt, debt1 = pick(bs, ["Total Debt"], 0), pick(bs, ["Total Debt"], 1)
        ocf, fcf = pick(cf, ["Operating Cash Flow"], 0), pick(cf, ["Free Cash Flow"], 0)

        sq, sy, pq, py = g(rev, rev1), g(rev, rev4), g(pat, pat1), g(pat, pat4)
        dchg = g(debt, debt1)

        bd = []  # score breakdown
        def add(factor, seen, pts): bd.append({"factor": factor, "seen": seen, "points": pts})
        add("Sales growth YoY", f"{sy:+.1f}% vs a year ago" if sy is not None else "No year-ago data", gscore(sy, 1.5))
        add("Sales growth QoQ", f"{sq:+.1f}% vs last quarter" if sq is not None else "No previous-quarter data", gscore(sq, 1))
        add("Profit growth YoY", f"{py:+.1f}% vs a year ago" if py is not None else "No year-ago data", gscore(py, 1.5))
        add("Profit growth QoQ", f"{pq:+.1f}% vs last quarter" if pq is not None else "No previous-quarter data", gscore(pq, 1))
        if opm is not None and opm4 is not None:
            d = round(opm - opm4, 1)
            add("Operating margin (OPM)", f"{opm}% now vs {opm4}% a year ago ({d:+.1f} points)",
                1 if d > 1 else -1 if d < -1 else 0)
        else:
            add("Operating margin (OPM)", "Not enough data", 0)
        if ocf is not None:
            if ocf < 0: add("Operating cash flow", "Negative: the business used cash this quarter", -2)
            elif pat and ocf >= 0.8 * pat: add("Operating cash flow", "Covers most of the profit", 1)
            else: add("Operating cash flow", "Positive but below profit", 0)
        else:
            add("Operating cash flow", "Not available", 0)
        if debt and debt1:
            if debt > debt1 * 1.15: add("Debt", f"Up {dchg:+.0f}% in the quarter", -1)
            else: add("Debt", f"Change {dchg:+.0f}% in the quarter", 0)
        else:
            add("Debt", "Not available", 0)

        score = sum(b["points"] for b in bd)
        verdict = "GOOD" if score >= 4 else "VERY BAD" if score <= -3 else "NEUTRAL"
        strengths = [b["seen"] for b in bd if b["points"] > 0]
        weaknesses = [b["seen"] for b in bd if b["points"] < 0]
        if verdict == "GOOD":
            summary = "Good quarter: " + "; ".join(strengths[:3]) + "." if strengths else "Good quarter."
            nxt = "To stay GOOD, watch for falling margins or negative operating cash flow next quarter."
        elif verdict == "VERY BAD":
            summary = "Very weak quarter: " + "; ".join(weaknesses[:3]) + "." if weaknesses else "Very weak quarter."
            nxt = "A recovery needs profit back in growth and operating cash flow turning positive."
        else:
            pos_t = "; ".join(strengths[:2]) or "none clear"
            neg_t = "; ".join(weaknesses[:2]) or "none clear"
            summary = f"Mixed quarter. Positives: {pos_t}. Negatives: {neg_t}."
            nxt = f"Score {score}. It needs about {max(0, 4 - score)} more points to become GOOD, from faster profit growth, a rising margin or stronger cash flow."

        history = []
        for c in range(min(4, inc.shape[1])):
            rv = pick(inc, REV, c); pt = pick(inc, PAT, c); eb = pick(inc, EBT, c)
            history.append({"q": pd.Timestamp(inc.columns[c]).strftime("%b %Y"), "sales": cr(rv), "pat": cr(pt),
                            "opm": round(eb / rv * 100, 1) if eb is not None and rv else None,
                            "debt": cr(pick(bs, ["Total Debt"], c)), "ocf": cr(pick(cf, ["Operating Cash Flow"], c))})

        present.append({"symbol": sym, "name": name, "fno": x.get("fno", False), "quarter_end": qend.date().isoformat(), "reported_on": reported,
                        "verdict": verdict, "score": score, "summary": summary, "next_step": nxt,
                        "breakdown": bd, "strengths": strengths, "weaknesses": weaknesses, "history": history,
                        "sales": cr(rev), "sales_qoq": sq, "sales_yoy": sy,
                        "pat": cr(pat), "pat_qoq": pq, "pat_yoy": py,
                        "ebitda": cr(ebitda), "ebitda_yoy": g(ebitda, ebitda4),
                        "opm": opm, "eps": round(eps, 2) if eps is not None else None,
                        "debt": cr(debt), "debt_chg": dchg, "ocf": cr(ocf), "fcf": cr(fcf),
                        "reasons": [b["factor"] + ": " + b["seen"] for b in bd if b["points"] != 0],
                        "filings_url": f"https://www.nseindia.com/get-quotes/equity?symbol={sym}"})
    except Exception as e:
        errors += 1
        print(sym, "skip:", str(e)[:80])

present.sort(key=lambda r: r.get("reported_on") or r["quarter_end"], reverse=True)
upcoming.sort(key=lambda r: r["next_date"])
json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "present": present, "upcoming": upcoming},
          open("results.json", "w"), indent=1, default=str)
print("present:", len(present), "upcoming:", len(upcoming), "errors:", errors)
