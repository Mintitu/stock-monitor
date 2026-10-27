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
TICKERS = list(dict.fromkeys(x.strip().upper() for x in os.getenv("TICKERS", "ARR,DX,REFI,SCM,PFLT,AGNC,ARE,CAG,MAIN,ARCC,BXSL,TSLX,GBDC,GLAD,TRIN").split(",") if x.strip()))
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
                    bars.append({"date": date.strftime("%Y-%m-%d"), "close": close})
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
                bars = [{"date": v["datetime"][:10], "close": num(v.get("close"))} for v in d["values"]]
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
    # Yahoo coverage reaching the last completed exchange session is accepted,
    # with the provider's incompleteness explicitly disclosed in the report.
    if y and fresh(y) and y.get("dividends_available"):
        start = pd.Timestamp(y["coverage_start"], tz="UTC")
        end = pd.Timestamp(y["coverage_end"], tz="UTC")
        if start <= cutoff and (today - end).days <= 5:
            value = sum(e["amount"] for e in y.get("events", []) if cutoff < pd.Timestamp(e["date"], tz="UTC") <= today)
            return value, "Yahoo (provider history; not independently verified)", "", datetime.fromtimestamp(y["fetched_at"], timezone.utc).isoformat()
    local = local_dividends(ticker, today, cutoff, events, coverage)
    if local is not None:
        return local, "Local verified coverage", "", "local input"
    return None, "unavailable", "Dividend history missing, stale or incomplete; not assumed zero", "n/a"


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
        return f"{path.name}: Telegram transport/response error; not retried to avoid duplicates"


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
        # Restrict symbols to US-style tickers; never interpret symbols as paths.
        if not ticker or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in ticker):
            raise ValueError("Invalid ticker format")
        history, source, yahoo, flags = choose_prices(ticker)
        row = {"ticker": ticker, "price_usd": None, "price_date": "n/a", "price_source": source,
               "price_fetched_at_utc": "n/a", "latest_period": "n/a"}
        if history:
            h = pd.DataFrame(history["bars"])
            h["timestamp"] = pd.to_datetime(h.date, errors="coerce", utc=True)
            h = h.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp")
            h = h[h.timestamp < today]
            h.to_csv(OUT / f"history_{ticker}.csv", index=False)
            if not h.empty:
                last = h.iloc[-1]
                row["price_usd"] = num(last["close"])
                row["price_date"] = last["date"]
                if (today - last.timestamp).days > 5:
                    flags.append("Price older than 5 calendar days")
            row["price_fetched_at_utc"] = datetime.fromtimestamp(history["fetched_at"], timezone.utc).isoformat()
        dv, ds, error, fetched = dividend_value(ticker, yahoo, today, cutoff, events, coverage)
        row.update({"dividends_ttm_per_share_usd": dv, "dividend_source": ds, "dividend_fetched_at_utc": fetched,
                    "yield_ttm_pct": dv / row["price_usd"] * 100 if dv is not None and row["price_usd"] else None})
        if error:
            flags.append(error)
        if dv is not None and ds.startswith("Yahoo"):
            flags.append("Yahoo dividend completeness not independently verified")
        if yahoo and yahoo.get("events"):
            pd.DataFrame(yahoo["events"]).to_csv(OUT / f"dividend_events_{ticker}.csv", index=False)
        fs = fundamentals[(fundamentals.ticker == ticker) & fundamentals.date.notna() & (fundamentals.date <= today)].sort_values("date").drop_duplicates("date", keep="last")
        if fundamentals[(fundamentals.ticker == ticker) & fundamentals.date.isna()].shape[0]:
            flags.append("Invalid fundamental period; use YYYY-MM-DD")
        for col in fcols[2:]:
            row[col] = None
            row[col + "_change_pct"] = None
        if not fs.empty:
            last = fs.iloc[-1]
            row["latest_period"] = str(last.period)
            for col in fcols[2:]:
                row[col] = num(last[col])
                row[col + "_change_pct"] = pct(fs.iloc[-2][col], last[col]) if len(fs) > 1 else None
                if row[col + "_change_pct"] is not None and row[col + "_change_pct"] < 0:
                    flags.append(col + " down")
            if (today - last.date).days > 150:
                flags.append("Fundamentals older than 150 days")
        else:
            flags.append("Missing official fundamentals")
        row["flags"] = "; ".join(flags) or "Check official filings"
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "snapshot.csv", index=False)
    cols = ["ticker", "price_usd", "price_date", "price_source", "dividends_ttm_per_share_usd", "dividend_source", "yield_ttm_pct", "latest_period", "flags"]
    lines = ["# Stock Watch - hybrid sources", "", datetime.now(timezone.utc).isoformat(), "",
        "Prices: daily Close, split-adjusted but NOT dividend-adjusted. Today's bar excluded, even after close.",
        "Each ticker's full price window comes from one provider; histories are not spliced across providers.",
        "Yahoo dividend events may be incomplete: the report labels provider data, not independently verified data.",
        "TTM uses ex-dividend dates in the last 365 days, not payment dates. Yield is not a forward guarantee.",
        "Corporate-action consistency and source differences require review. Do not assume precise total returns.",
        "Local fallback requires full date-window coverage and dividends normalized to current share basis.",
        "NII/NAV are manual official inputs; no EPS substitutions. Changes compare two entered periods, not CAGR.",
        "Informational screening only; not a buy/sell recommendation.", "",
        "|" + "|".join(cols) + "|", "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in rows:
        lines.append("|" + "|".join("n/a" if row.get(c) is None else str(row.get(c, "n/a")).replace("|", "/") for c in cols) + "|")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(16, max(6, len(rows) * .45 + 2)))
    ax.axis("off")
    cells = [[r["ticker"], fmt(r["price_usd"]), r["price_date"], fmt(r["yield_ttm_pct"]), r["price_source"], "Yahoo*" if r["dividend_source"].startswith("Yahoo") else r["dividend_source"]] for r in rows]
    tab = ax.table(cellText=cells, colLabels=["Ticker", "Close USD", "Price date", "TTM yield %", "Price source", "Dividend source"], loc="center", cellLoc="center")
    tab.auto_set_font_size(False)
    tab.set_fontsize(9)
    tab.scale(1, 1.6)
    ax.set_title("Stock Watch | hybrid sources\n*Yahoo dividend completeness not independently verified | n/a = unavailable\nSee report and CSV for dates and quality flags", fontsize=12)
    fig.savefig(OUT / "dashboard.png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    status = [f"Yahoo history calls: {STATE['yahoo_calls']} (not necessarily HTTP requests)", f"Twelve Data requests: {STATE['td_calls']}", send_file(OUT / "dashboard.png", True)]
    if os.getenv("SEND_FILES", "1") == "1":
        status += [send_file(OUT / n) for n in ("report.md", "snapshot.csv")]
    (OUT / "delivery-status.txt").write_text("\n".join(status))
    print("\n".join(status))
    if not df.price_usd.notna().any():
        raise RuntimeError("No usable prices. Inspect cache and provider access; reports saved.")


if __name__ == "__main__":
    main()
