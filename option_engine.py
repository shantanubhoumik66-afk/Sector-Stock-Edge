"""Option engine: NSE option-chain snapshots (indices + F&O stocks) -> option.json. Snapshot, not tick data."""
import json, time, datetime as dt, traceback
import requests

H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

def fail(msg): print("STOPPED:", msg); raise SystemExit(msg)

s = requests.Session(); s.headers.update(H)
try:
    s.get("https://www.nseindia.com", timeout=20)
    s.get("https://www.nseindia.com/option-chain", timeout=20)
except Exception as e: print("warm-up failed:", e)

try: stocks = json.load(open("stocks.json")).get("stocks", [])
except Exception: stocks = []
fo_stocks = sorted({x["symbol"] for x in stocks if x.get("fno")})
if not fo_stocks: print("Note: stocks.json has no F&O list yet; indices only.")

def chain(sym, is_index):
    kind = "Indices" if is_index else "Equity"
    urls = [
        f"https://www.nseindia.com/api/option-chain-indices?symbol={sym}" if is_index else f"https://www.nseindia.com/api/option-chain-equities?symbol={sym}",
        f"https://www.nseindia.com/api/option-chain-v3?type={kind}&symbol={sym}",
    ]
    msgs = []
    for u in urls:
        try:
            r = s.get(u, headers={"Referer": "https://www.nseindia.com/option-chain"}, timeout=30)
        except Exception as e:
            msgs.append(f"request error ({str(e)[:60]})"); continue
        if r.status_code != 200:
            msgs.append(f"HTTP {r.status_code}"); continue
        if not r.text.strip().startswith("{"):
            msgs.append("web page instead of data"); continue
        js = r.json()
        if "records" in js and js["records"].get("data"):
            return js
        msgs.append("empty chain")
    raise RuntimeError(" / ".join(msgs))

def num(x):
    try: return float(x)
    except Exception: return 0.0

def analyse(sym, js, is_index):
    rec = js["records"]; exp = rec["expiryDates"][0]; und = num(rec.get("underlyingValue"))
    rows = [d for d in rec["data"] if d.get("expiryDate") == exp]
    strikes = {}
    for d in rows:
        k = num(d["strikePrice"]); ce = d.get("CE") or {}; pe = d.get("PE") or {}
        strikes[k] = {"strike": k, "ce_oi": num(ce.get("openInterest")), "ce_chg": num(ce.get("changeinOpenInterest")),
                      "pe_oi": num(pe.get("openInterest")), "pe_chg": num(pe.get("changeinOpenInterest"))}
    S = sorted(strikes.values(), key=lambda x: x["strike"])
    ce_oi = sum(x["ce_oi"] for x in S); pe_oi = sum(x["pe_oi"] for x in S)
    ce_chg = sum(x["ce_chg"] for x in S); pe_chg = sum(x["pe_chg"] for x in S)
    # Max pain: the expiry price where option writers pay the least
    def pain(K):
        return sum(max(0, K - x["strike"]) * x["ce_oi"] + max(0, x["strike"] - K) * x["pe_oi"] for x in S)
    max_pain = min((x["strike"] for x in S), key=pain) if S else None
    # Call vs put writer verdict from the change in OI
    if ce_chg <= 0 and pe_chg <= 0:
        verdict = "UNWINDING (both sides reducing OI)"
    elif ce_chg > 1.2 * max(pe_chg, 1):
        verdict = "CALL WRITER DOMINANT (bearish pressure)"
    elif pe_chg > 1.2 * max(ce_chg, 1):
        verdict = "PUT WRITER DOMINANT (bullish support)"
    else:
        verdict = "BALANCED"
    # Strikes near the money
    atm = min(S, key=lambda x: abs(x["strike"] - und)) if S and und else None
    idx = S.index(atm) if atm else 0
    near = S[max(0, idx - 4): idx + 5]
    resistance = [x["strike"] for x in sorted(S, key=lambda x: -x["ce_oi"])[:3]]
    support = [x["strike"] for x in sorted(S, key=lambda x: -x["pe_oi"])[:3]]
    return {"symbol": sym, "name": sym + (" Index" if is_index else ""), "type": "INDEX" if is_index else "STOCK",
            "expiry": exp, "underlying": und, "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None,
            "ce_oi": ce_oi, "pe_oi": pe_oi, "ce_chg": ce_chg, "pe_chg": pe_chg, "max_pain": max_pain,
            "verdict": verdict, "resistance": resistance, "support": support,
            "strikes": [{k: round(v, 0) if isinstance(v, float) else v for k, v in x.items()} for x in near]}

items, errors = {}, []
for sym in INDICES:
    try: items[sym] = analyse(sym, chain(sym, True), True)
    except Exception as e: errors.append(f"{sym}: {e}")
    time.sleep(0.3)
for sym in fo_stocks:
    try: items[sym] = analyse(sym, chain(sym, False), False)
    except Exception as e: errors.append(f"{sym}: {e}")
    time.sleep(0.3)

if errors:
    print("First errors:")
    for e in errors[:5]: print("  ", e)
if not items:
    fail("No option-chain data returned. See 'First errors' above for the reason.")
json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "items": items, "errors": len(errors)},
          open("option.json", "w"), indent=1, default=str)
print("option items:", len(items), "errors:", len(errors))
