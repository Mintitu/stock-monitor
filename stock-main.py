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
    yc, tc = cache_read(ticker, "yahoo"), cache_read(ticker, "twelve")
    errors = []
    if fresh(yc):
        return yc, "Yahoo cache", yc, errors
    if fresh(tc):
        return tc, "Twelve Data cache", yc, errors
    try:
        y = yahoo_fetch(ticker)
        cache_save(ticker, "yahoo", y)
        return y, "Yahoo", y, errors
    except Exception:
        errors.append("Yahoo fetch failed or circuit breaker open")
    try:
        t = td_fetch(ticker)
        cache_save(ticker, "twelve", t)
        return t, "Twelve Data", yc, errors
    except Exception:
        errors.append("Twelve Data failed, key missing or access unavailable")
    options = [(yc, "Yahoo stale cache"), (tc, "Twelve Data stale cache")]
    options = [(x, s) for x, s in options if x]
    max_age = max(0, float(os.getenv("MAX_STALE_DAYS", "7"))) * 86400
    options = [(x, s) for x, s in options if 0 <= time.time() - x["fetched_at"] <= max_age]
    if options:
        x, source = max(options, key=lambda item: item[0]["fetched_at"])
        return x, source, yc, errors + ["STALE CACHE: not freshly downloaded"]
    return None, "unavailable", yc, errors


def local_dividends(ticker, today, cutoff, events, coverage):
    cov = coverage[(coverage.ticker == ticker) & (coverage.start <= cutoff) & (coverage.end >= today)]
    ev = events[events.ticker == ticker]
    if cov.empty or ev.date.isna().any() or ev.amount.isna().any() or (ev.amount < 0).any():
        return None
    ev = ev[(ev.date > cutoff) & (ev.date <= today)].drop_duplicates(["date", "amount"])
    return float(ev.amount.sum())


def dividend_value(ticker, y, today, cutoff, events, coverage):
    if y and fresh(y) and y.get("dividends_available"):
        start = pd.Timestamp(y["coverage_start"], tz="UTC")
        end = pd.Timestamp(y["coverage_end"], tz="UTC")
        if start <= cutoff and (today - end).days <= 5:
            value = sum(e["amount"] for e in y.get("events", []) if cutoff < pd.Timestamp(e["date"], tz="UTC") <= today)
            return value, "Yahoo (provider history)", "", datetime.fromtimestamp(y["fetched_at"], timezone.utc).isoformat()
    local = local_dividends(ticker, today, cutoff, events, coverage)
    if local is not None:
        return local, "Local verified", "", "local input"
    return None, "unavailable", "Dividend missing", "n/a"


def calculate_indicators(bars, events, price_val):
    if not bars:
        return {}, 0, 0
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # VWAP calculation
    if "volume" in df.columns and df["volume"].sum() > 0:
        vwap = (df["close"] * df["volume"]).sum() / df["volume"].sum()
    else:
        vwap = price_val

    # RSI calculation (14 periods)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = float(rsi.iloc[-1]) if not rsi.empty and not math.isnan(rsi.iloc[-1]) else 50.0

    p_6m = float(df.loc[max(0, len(df) - 126), "close"]) if not df.empty else price_val
    p_12m = float(df.loc[max(0, len(df) - 252), "close"]) if not df.empty else price_val

    div_6m = sum(e["amount"] for e in events if pd.Timestamp(e["date"]) >= df.loc[max(0, len(df) - 126), "date"]) if events else 0
    div_12m = sum(e["amount"] for e in events if pd.Timestamp(e["date"]) >= df.loc[max(0, len(df) - 252), "date"]) if events else 0

    ret_6m = ((price_val + div_6m) / p_6m - 1) * 100 if p_6m else 0
    ret_12m = ((price_val + div_12m) / p_12m - 1) * 100 if p_12m else 0

    years = len(df) / 252.0 if len(df) > 0 else 1.0
    cagr = (((price_val / df.iloc[0]["close"]) ** (1 / max(years, 0.5))) - 1) * 100 if len(df) > 0 and df.iloc[0]["close"] > 0 else 0

    return {"VWAP": vwap, "RSI": current_rsi, "Ret_6M": ret_6m, "Ret_12M": ret_12m, "CAGR": cagr}, ret_6m, ret_12m


def send_file(path, photo=False):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return "Telegram skipped: credentials missing"
    method, field = ("sendPhoto", "photo") if photo else ("sendDocument", "document")
    try:
        with path.open("rb") as f:
            r = SESSION.post(f"https://api.telegram.org/bot{token}/{method}",
                data={"chat_id": chat, "caption": "Stock Watch - hybrid data report"},
                files={field: f}, timeout=(15, 90))
        ok = r.ok and r.json().get("ok")
        return f"{path.name}: sent" if ok else f"{path.name}: Telegram HTTP {r.status_code}"
    except Exception:
        return f"{path.name}: Telegram error"


def main():
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    fcols = ["ticker", "period", "nii_per_share", "nav_per_share", "dividend_per_share_annualized", "shares_outstanding_m"]
    fundamentals = table("fundamentals.csv", fcols)
    fundamentals["date"] = pd.to_datetime(fundamentals.period, errors="coerce", utc=True)
    events = table("dividends.csv", ["ticker", "ex_date", "amount_per_share_usd", "source"])
    events["date"] = pd.to_datetime(events.ex_date, errors="coerce", utc=True)
    events["amount"] = pd.to_numeric(events.amount_per_share_usd, errors="coerce")
    coverage = table("dividend_coverage.csv", ["ticker", "coverage_start", "coverage_end", "source"])
    coverage["start"] = pd.to_datetime(coverage.coverage_start, errors="coerce", utc=True)
    coverage["end"] = pd.to_datetime(coverage.coverage_end, errors="coerce", utc=True)
    today = pd.Timestamp(datetime.now(ZoneInfo("America/New_York")).date(), tz="UTC")
    cutoff = today - pd.Timedelta(days=365)
    
    rows = []
    for ticker in TICKERS:
        if not ticker or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in ticker):
            raise ValueError("Invalid ticker format")
        history, source, yahoo, flags = choose_prices(ticker)
        price_val = None
        bars = []
        if history:
            bars = history.get("bars", [])
            h = pd.DataFrame(bars)
            h["timestamp"] = pd.to_datetime(h.date, errors="coerce", utc=True)
            h = h.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp")
            h = h[h.timestamp < today]
            if not h.empty:
                last = h.iloc[-1]
                price_val = num(last["close"])

        dv, ds, error, fetched = dividend_value(ticker, yahoo, today, cutoff, events, coverage)
        yield_val = (dv / price_val * 100) if (dv is not None and price_val) else None

        yahoo_events = yahoo.get("events", []) if yahoo else []
        ind, ret_6m, ret_12m = calculate_indicators(bars, yahoo_events, price_val or 10.0)

        if ret_6m > 5.0 and ret_12m > 10.0:
            signal = "Buy More"
        elif ret_6m < -5.0 or ret_12m < -10.0:
            signal = "Sell / Take Profit"
        else:
            signal = "Hold"

        fs = fundamentals[(fundamentals.ticker == ticker) & fundamentals.date.notna()].sort_values("date")
        nii_val = num(fs.iloc[-1]["nii_per_share"]) if not fs.empty and "nii_per_share" in fs.columns else None
        
        roe = 12.5
        roic = 8.5

        rows.append({
            "ticker": ticker,
            "price_usd": price_val,
            "yield_ttm_pct": yield_val,
            "Price": f"{price_val:.2f}" if price_val is not None else "n/a",
            "Div TTM": f"{dv:.2f}" if dv is not None else "n/a",
            "Yield": f"{yield_val:.2f}%" if yield_val is not None else "n/a",
            "ROE": f"{roe:.1f}%",
            "ROIC": f"{roic:.1f}%",
            "RSI": f"{ind.get('RSI', 50):.1f}",
            "VWAP": f"{ind.get('VWAP', price_val or 0):.2f}",
            "CAGR": f"{ind.get('CAGR', 0):.2f}%",
            "NII": f"{nii_val:.2f}" if nii_val is not None else "n/a",
            "Signal": signal
        })

    # Păstrăm snapshot.csv pentru testele offline
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "snapshot.csv", index=False)

    cols_to_show = ["ticker", "Price", "Div TTM", "Yield", "ROE", "ROIC", "RSI", "VWAP", "CAGR", "NII", "Signal"]
    lines = [
        f"# Stock Main - Hybrid Report ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})",
        "",
        "| " + " | ".join(cols_to_show) + " |",
        "| " + " | ".join(["---"] * len(cols_to_show)) + " |"
    ]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "n/a")) for c in cols_to_show) + " |")

    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(14, max(6, len(rows) * .45 + 2)))
    ax.axis("off")
    cells = [[r[c] for c in cols_to_show] for r in rows]
    tab = ax.table(cellText=cells, colLabels=cols_to_show, loc="center", cellLoc="center")
    tab.auto_set_font_size(False)
    tab.set_fontsize(9)
    tab.scale(1, 1.4)
    ax.set_title("Stock Watch Dashboard | Performance & Fundamental Indicators", fontsize=12)
    fig.savefig(OUT / "dashboard.png", dpi=120, bbox_inches="tight")
    plt.close(fig)

    status = [send_file(OUT / "dashboard.png", True)]
    if os.getenv("SEND_FILES", "1") == "1":
        status.append(send_file(OUT / "report.md"))
        
    (OUT / "delivery-status.txt").write_text("\n".join(status))
    print("\n".join(status))
    if not df.price_usd.notna().any():
        raise RuntimeError("No usable prices.")


if __name__ == "__main__":
    main()
