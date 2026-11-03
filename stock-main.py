import os
import json
import time
import math
import logging
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CACHE = ROOT / ".data-cache"

default_tickers = "ARR,DX,REFI,SCM,PFLT,AGNC,ARE,CAG,MAIN,ARCC,SPCX,KGS,PEP,QYLD,VICI,AVGO"
TICKERS = list(dict.fromkeys(x.strip().upper() for x in os.getenv("TICKERS", default_tickers).split(",") if x.strip()))

SESSION = requests.Session()
STATE = {"yahoo_failures": 0, "td_disabled": False, "td_last": 0.0, "yahoo_calls": 0, "td_calls": 0}
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def num(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def fmt(v):
    n = num(v)
    return "n/a" if n is None else f"{n:.2f}"


def pct(a, b):
    a, b = num(a), num(b)
    return None if a in (None, 0) or b is None else (b / a - 1) * 100


def table(name, columns):
    p = ROOT / name
    if not p.exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(p, comment="#")
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{name}: missing columns {sorted(missing)}")
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    return df


def cache_read(ticker, source):
    try:
        x = json.loads((CACHE / f"{ticker}-{source}.json").read_text())
        return x if valid_prices(x) else None
    except (ValueError, OSError, TypeError):
        return None


def cache_save(ticker, source, data):
    p = CACHE / f"{ticker}-{source}.json"
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(data, allow_nan=False))
    temp.replace(p)


def fresh(x):
    return bool(x) and 0 <= time.time() - x["fetched_at"] < max(0, float(os.getenv("CACHE_TTL_HOURS", "6"))) * 3600


def valid_prices(x):
    return isinstance(x, dict) and x.get("currency") == "USD" and bool(x.get("bars")) and all(num(b.get("close")) is not None and num(b["close"]) > 0 for b in x["bars"])


def yahoo_fetch(ticker):
    if STATE["yahoo_failures"] >= 3:
        raise RuntimeError("Yahoo circuit breaker open")
    for attempt in range(2):
        time.sleep(1.2 if attempt == 0 else 5)
        STATE["yahoo_calls"] += 1
        try:
            obj = yf.Ticker(ticker)
            h = obj.history(period="2y", interval="1d", auto_adjust=False,
                            back_adjust=False, actions=True, raise_errors=True, timeout=25)
            meta = obj.get_history_metadata() or {}
            if h.empty or meta.get("currency") != "USD":
                raise ValueError("Empty history or unexpected currency")
            bars, events = [], []
            for date, row in h.iterrows():
                close = num(row.get("Close"))
                if close is not None and close > 0:
                    bars.append({"date": date.strftime("%Y-%m-%d"), "close": close, "volume": num(row.get("Volume")) or 0})
                amount = num(row.get("Dividends"))
                if amount is not None and amount > 0:
                    events.append({"date": date.strftime("%Y-%m-%d"), "amount": amount})
            if not bars:
                raise ValueError("No valid prices")
            result = {"fetched_at": time.time(), "currency": "USD", "bars": bars,
                      "events": events, "dividends_available": "Dividends" in h.columns,
                      "coverage_start": min(b["date"] for b in bars),
                      "coverage_end": max(b["date"] for b in bars),
                      "adjustment": "split-adjusted, not dividend-adjusted"}
            STATE["yahoo_failures"] = 0
            return result
        except Exception:
            pass
    STATE["yahoo_failures"] += 1
    raise RuntimeError("Yahoo unavailable or invalid history")


def td_fetch(ticker):
    key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not key or STATE["td_disabled"]:
        raise RuntimeError("Twelve Data key missing or provider disabled")
    for attempt in range(3):
        time.sleep(max(0, 8.2 - (time.monotonic() - STATE["td_last"])))
        STATE["td_last"] = time.monotonic()
        STATE["td_calls"] += 1
        code = None
        try:
            r = SESSION.get("https://api.twelvedata.com/time_series",
                headers={"Authorization": f"apikey {key}"},
                params={"symbol": ticker, "country": "United States", "interval": "1day",
                        "outputsize": 400, "order": "asc", "adjust": "splits"}, timeout=(15, 40))
            d = r.json()
            code = int(d.get("code", r.status_code))
            if r.ok and d.get("status") != "error" and d.get("values"):
                bars = [{"date": v["datetime"][:10], "close": num(v.get("close")), "volume": num(v.get("volume")) or 0} for v in d["values"]]
                x = {"fetched_at": time.time(), "currency": d.get("meta", {}).get("currency"),
                     "bars": bars, "adjustment": "split-adjusted, not dividend-adjusted"}
                if not valid_prices(x):
                    raise RuntimeError("Invalid Twelve Data prices/currency")
                return x
            if code == 401:
                STATE["td_disabled"] = True
            if code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Twelve Data HTTP/API {code}")
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass
        if attempt < 2:
            time.sleep(65 if code == 429 else 5 * 2 ** attempt)
    raise RuntimeError("Twelve Data unavailable after bounded retries")


def choose_prices(ticker):
    yc, tc = cache_read(ticker, "yahoo"), cache_
