"""News engine: stock-specific news from genuine exchange sources -> news.json.
Sources: NSE corporate announcements (orders, contracts, results, corporate actions) and NSE bulk and block deal files.
Sentiment is rule-based from the filing text. Not a prediction."""
import io, re, json, datetime as dt
import pandas as pd, requests

H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
DAYS = 30
NOW = dt.datetime.now()
PTS = {"VERY POSITIVE": 2, "POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1, "VERY NEGATIVE": -2}

def weight(age_days):
    if age_days is None: return 0.5
    if age_days <= 7: return 1.0
    if age_days <= 15: return 0.6
    if age_days <= DAYS: return 0.3
    return 0

def value_cr(text):
    m = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|million|mn|billion|bn)", text, re.I)
    if not m: return None
    mult = {"crore": 1, "cr": 1, "million": 0.1, "mn": 0.1, "billion": 100, "bn": 100}[m.group(2).lower()]
    return float(m.group(1).replace(",", "")) * mult

def classify(text):
    """Return (sentiment, type) for a filing, or (None, None) if it isn't news we track."""
    t = text.lower()
    if re.search(r"fraud|insolven|default|sebi order|show cause|arrest", t): return "VERY NEGATIVE", "Governance / regulatory"
    if re.search(r"penalty|resign|downgrade|pledge|notice", t): return "NEGATIVE", "Governance / regulatory"
    if re.search(r"order|contract|award|letter of (intent|award)", t):
        v = value_cr(t)
        return ("VERY POSITIVE" if v and v >= 500 else "POSITIVE"), "Order / contract"
    if re.search(r"buyback|bonus|dividend|split", t): return "POSITIVE", "Corporate action"
    if re.search(r"acquisition|merger|demerger|stake", t): return "NEUTRAL", "Corporate action"
    if re.search(r"financial result|quarterly result|results", t): return "NEUTRAL", "Results"
    return None, None

def impact_label(score):
    if score >= 2: return "VERY POSITIVE"
    if score >= 1: return "POSITIVE"
    if score <= -2: return "VERY NEGATIVE"
    if score <= -1: return "NEGATIVE"
    return "NEUTRAL"

def fetch_announcements(s, names):
    out, today = [], dt.date.today()
    for k in range(2):
        to = today - dt.timedelta(15 * k); fr = to - dt.timedelta(15)
        try:
            js = s.get("https://www.nseindia.com/api/corporate-announcements", headers={"Referer": "https://www.nseindia.com/"}, timeout=30,
                       params={"index": "equities", "from_date": fr.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")}).json()
            items = js if isinstance(js, list) else js.get("data", [])
        except Exception as e:
            print("announcements failed:", str(e)[:80]); items = []
        for i in items:
            sym = str(i.get("symbol", "")).strip()
            if sym not in names: continue
            desc = str(i.get("desc") or ""); text = str(i.get("attchmntText") or "")
            full = desc + " " + text
            # Only keep filings that name this company, so another company's filing never appears here
            first = names[sym].split()[0].lower() if names[sym] else sym.lower()
            if first not in full.lower() and first not in sym.lower(): continue
            sent, typ = classify(full)
            if not sent: continue
            date = str(i.get("an_dt") or "")[:10]
            out.append({"symbol": sym, "date": date, "type": typ, "title": desc.strip() or typ,
                        "text": text[:240], "source": "NSE corporate filing", "link": str(i.get("attchmntFile") or ""),
                        "sentiment": sent, "age": age_of(date)})
    return out

def age_of(date_str):
    try: return (NOW.date() - dt.date.fromisoformat(date_str[:10])).days
    except Exception: return None

def fetch_deals(url, kind, names):
    out = []
    try:
        d = pd.read_csv(io.StringIO(requests.get(url, headers=H, timeout=30).text), skipinitialspace=True)
    except Exception as e:
        print(kind, "deal file failed:", str(e)[:80]); return out
    d.columns = [c.strip().lower() for c in d.columns]
    col = lambda pre: next((c for c in d.columns if c.startswith(pre)), None)
    c_sym, c_side, c_qty, c_px, c_cli, c_date = col("symbol"), col("buy"), col("quantity"), col("trade price") or col("wght") or col("price"), col("client"), col("date")
    if not (c_sym and c_side and c_qty and c_px): print(kind, "deal columns not as expected:", list(d.columns)); return out
    for _, r in d.iterrows():
        sym = str(r[c_sym]).strip()
        if sym not in names: continue
        try: cr = float(r[c_qty]) * float(r[c_px]) / 1e7
        except Exception: continue
        if cr < 10: continue
        buy = str(r[c_side]).strip().upper().startswith("B")
        sent = ("VERY POSITIVE" if buy else "VERY NEGATIVE") if cr >= 100 else ("POSITIVE" if buy else "NEGATIVE")
        date = pd.to_datetime(r[c_date], dayfirst=True, errors="coerce") if c_date else pd.NaT
        date = date.strftime("%Y-%m-%d") if not pd.isna(date) else NOW.strftime("%Y-%m-%d")
        client = str(r[c_cli]).strip() if c_cli else "client"
        out.append({"symbol": sym, "date": date, "type": f"{kind} deal", "title": f"{'Buy' if buy else 'Sell'} {r[c_qty]} shares at ₹{r[c_px]} (about ₹{cr:,.0f} cr) by {client}",
                    "text": "", "source": f"NSE {kind} deals file", "link": url, "sentiment": sent, "age": age_of(date)})
    return out

def summarise(items):
    score = sum(PTS[i["sentiment"]] * weight(i["age"]) for i in items)
    ranked = sorted(items, key=lambda i: -abs(PTS[i["sentiment"]]) * weight(i["age"]))
    drivers = "; ".join(i["title"][:90] for i in ranked[:2] if PTS[i["sentiment"]] != 0)
    reason = f"Main drivers: {drivers}." if drivers else "No strong positive or negative items in the last 30 days."
    return round(score, 2), impact_label(score), reason

def main():
    stocks = json.load(open("stocks.json")).get("stocks", []) if __import__("os").path.exists("stocks.json") else []
    names = {x["symbol"]: x.get("name", x["symbol"]) for x in stocks}
    s = requests.Session(); s.headers.update(H)
    try: s.get("https://www.nseindia.com", timeout=20)
    except Exception: pass
    items = fetch_announcements(s, names)
    items += fetch_deals("https://nsearchives.nseindia.com/content/equities/bulk.csv", "bulk", names)
    items += fetch_deals("https://nsearchives.nseindia.com/content/equities/block.csv", "block", names)
    by = {}
    for it in items:
        if weight(it["age"]) > 0: by.setdefault(it["symbol"], []).append(it)
    out = {}
    for sym, lst in by.items():
        lst.sort(key=lambda i: i["date"], reverse=True)
        score, label, reason = summarise(lst)
        out[sym] = {"impact": label, "score": score, "impact_reason": reason, "days": DAYS, "items": lst[:25]}
    json.dump({"updated": NOW.isoformat(), "days": DAYS, "stocks": out}, open("news.json", "w"), indent=1, default=str)
    print("stocks with news:", len(out), "items:", len(items))

if __name__ == "__main__":
    main()
