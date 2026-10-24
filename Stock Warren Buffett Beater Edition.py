#!/usr/bin/env python3
"""Nasdaq + NYSE High-Performance Quantitative Scanner (Buffett-Beater Edition)."""
import os, re, time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output'
OUT.mkdir(exist_ok=True)

NASDAQ_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
OTHER_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'

MIN_PRICE = float(os.getenv('MIN_PRICE_USD', '7'))  # Urcat la $7 pentru a tăia mizeriile penny-stocks
MIN_DOLLAR_VOL = float(os.getenv('MIN_AVG_DOLLAR_VOLUME', '8000000'))  # Lichiditate instituțională min $8M/zi
MIN_ROWS = int(os.getenv('MIN_HISTORY_ROWS', '600'))
BATCH_SIZE = int(os.getenv('SCAN_BATCH_SIZE', '40'))
UNIVERSE_LIMIT = int(os.getenv('SCAN_UNIVERSE_LIMIT', '0'))
TOP_N = 10  # Extins la Top 10 oportunități majore

NON_EQUITY_NAME = re.compile(
    r'\b(ETF|ETN|exchange.traded fund|fund|closed.end fund|mutual fund|warrant|rights?|units?|preferred|notes? due|debentures?|bonds?|income fund|capital trust|mortgage trust|commodity|currency|portfolio|index shares|acquisition corp(?:oration)?|acquisition company|blank check|SPAC)\b|%',
    re.I
)
NON_EQUITY_SUFFIX = re.compile(r'[./$=](?:W|WS|WT|U|UN|R|RT|P|PR[A-Z]?)$', re.I)

def log(s): print(f"[{datetime.now(timezone.utc):%H:%M:%S} UTC] {s}", flush=True)
def norm(s): return str(s or '').strip().upper()
def yh(s): return s.replace('.', '-')

def read_dir(url):
    r = requests.get(url, timeout=45, headers={'User-Agent': 'StockWatchMaster/2.0'})
    r.raise_for_status()
    lines = [x for x in r.text.splitlines() if x.strip() and not x.startswith('File Creation Time')]
    return pd.read_csv(StringIO('\n'.join(lines)), sep='|', dtype=str)

def eligible_symbol(symbol, name):
    symbol, name = norm(symbol), str(name or '').strip()
    if not symbol or not name or NON_EQUITY_NAME.search(name) or NON_EQUITY_SUFFIX.search(symbol): return False
    if re.search(r'\b(trust|income shares|income trust)\b', name, re.I): return False
    return bool(re.fullmatch(r'[A-Z0-9.-]{1,12}', symbol))

def universe():
    log('Descarc directoarele oficiale Nasdaq & NYSE...')
    n = read_dir(NASDAQ_URL); o = read_dir(OTHER_URL)
    n = n[n['Test Issue'].fillna('Y').str.upper().eq('N')].copy()
    if 'Financial Status' in n: n = n[n['Financial Status'].fillna('N').str.upper().eq('N')]
    n['symbol'] = n['Symbol'].map(norm); n['company'] = n['Security Name'].fillna(''); n['exchange'] = 'NASDAQ'
    
    o = o[o['Exchange'].eq('N') & o['ETF'].fillna('Y').str.upper().eq('N') & o['Test Issue'].fillna('Y').str.upper().eq('N')].copy()
    o['symbol'] = o['ACT Symbol'].map(norm); o['company'] = o['Security Name'].fillna(''); o['exchange'] = 'NYSE'
    
    u = pd.concat([n[['symbol', 'company', 'exchange']], o[['symbol', 'company', 'exchange']]], ignore_index=True).drop_duplicates('symbol')
    u = u[u.apply(lambda r: eligible_symbol(r.symbol, r.company), axis=1)].sort_values(['exchange', 'symbol']).reset_index(drop=True)
    if UNIVERSE_LIMIT > 0: u = u.head(UNIVERSE_LIMIT)
    return u

def rsi(s, p=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def extract(data, symbol, batch):
    if data is None or data.empty: return None
    if not isinstance(data.columns, pd.MultiIndex): return data if len(batch) == 1 else None
    for key in (yh(symbol), symbol):
        if key in data.columns.get_level_values(0): return data[key].copy()
        if key in data.columns.get_level_values(1): return data.xs(key, axis=1, level=1).copy()
    return None

def download(batch):
    tickers = [yh(x) for x in batch]
    for attempt, wait in enumerate((3, 8, 20), 1):
        try:
            d = yf.download(tickers=tickers, period='3y', interval='1d', auto_adjust=True, group_by='ticker', threads=True, progress=False, timeout=40, prepost=False)
            if d is not None and not d.empty: return d
        except Exception:
            pass
        if attempt < 3: time.sleep(wait)
    return pd.DataFrame()

def score(symbol, company, exchange, h):
    if h is None or h.empty: return None
    h = h.copy(); h.columns = [str(c).title() for c in h.columns]
    if not {'Open', 'High', 'Low', 'Close', 'Volume'}.issubset(h.columns): return None
    h = h.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume']).sort_index()
    h = h[~h.index.duplicated(keep='last')]
    if len(h) < MIN_ROWS: return None
    
    c = h.Close.astype(float); op = h.Open.astype(float); hi = h.High.astype(float); lo = h.Low.astype(float); vol = h.Volume.astype(float)
    price = float(c.iloc[-1]); dv = float((c * vol).tail(20).mean())
    if not np.isfinite(price) or price < MIN_PRICE or not np.isfinite(dv) or dv < MIN_DOLLAR_VOL: return None
    
    rv = rsi(c); r = float(rv.iloc[-1]) if pd.notna(rv.iloc[-1]) else np.nan
    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(c.rolling(50).mean().iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1])
    sma50prev = float(c.rolling(50).mean().iloc[-6])
    
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ms = m.ewm(span=9, adjust=False).mean()
    
    prev = float(c.iloc[-2])
    gap = (float(op.iloc[-1]) / prev - 1) * 100 if prev else np.nan
    day = (price / float(op.iloc[-1]) - 1) * 100 if float(op.iloc[-1]) else np.nan
    prior = float(vol.iloc[-21:-1].mean())
    rel = float(vol.iloc[-1] / prior) if prior > 0 else np.nan
    
    tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]); atrp = atr / price * 100
    
    def ret(days): return (float(c.iloc[-1]) / float(c.iloc[-days-1]) - 1) * 100 if len(c) > days and c.iloc[-days-1] else np.nan
    r3, r6, r12 = ret(63), ret(126), ret(252)
    
    # Motorul de Scor - Optimizat pentru Creștere Agresivă (Buffett Beater)
    trend = 20 * (price > sma50) + 15 * (sma50 > sma200) + 10 * (price > sma200) + 10 * (sma50 > sma50prev) + \
            10 * (np.isfinite(r3) and r3 > 0) + 10 * (np.isfinite(r6) and r6 > 5) + 10 * (np.isfinite(r12) and r12 > 10) + \
            5 * (np.isfinite(r) and 50 <= r <= 70) + 5 * (float(m.iloc[-1]) > float(ms.iloc[-1])) + 5 * (np.isfinite(rel) and rel >= 1.3)
            
    rebound = 0
    if np.isfinite(r): rebound += 20 if 30 <= r < 45 else 10 if 45 <= r < 52 else 5
    rebound += 15 * (price > ema20) + 15 * (day > 0) + 15 * (np.isfinite(rel) and rel >= 1.5) + 15 * (float(m.iloc[-1]) > float(ms.iloc[-1])) + 20 * (price > prev)
    
    combined = .60 * trend + .40 * rebound
    if np.isfinite(atrp) and atrp > 7: combined -= min(20, (atrp - 7) * 2.5) # Penalizare pentru volatilitate extremă nesănătoasă

    return {
        'ticker': symbol, 'company': company, 'exchange': exchange,
        'price_usd': round(price, 2), 'score': round(float(np.clip(combined, 0, 100)), 1),
        'strategy': 'Trend' if trend >= rebound else 'Rebound',
        'trend_score': round(float(trend), 1), 'rebound_score': round(float(rebound), 1),
        'rsi14': round(r, 2) if np.isfinite(r) else np.nan,
        'gap_pct': round(gap, 2) if np.isfinite(gap) else np.nan,
        'day_change_pct': round(day, 2) if np.isfinite(day) else np.nan,
        'relative_volume': round(rel, 2) if np.isfinite(rel) else np.nan,
        'avg_dollar_volume_20d': round(dv, 0),
        'ema20': round(ema20, 2), 'sma50': round(sma50, 2), 'sma200': round(sma200, 2),
        'macd_bullish': bool(float(m.iloc[-1]) > float(ms.iloc[-1])),
        'atr_pct': round(atrp, 2) if np.isfinite(atrp) else np.nan,
        'return_3m_pct': round(r3, 2) if np.isfinite(r3) else np.nan,
        'return_6m_pct': round(r6, 2) if np.isfinite(r6) else np.nan,
        'return_12m_pct': round(r12, 2) if np.isfinite(r12) else np.nan,
        'history_rows': len(h), 'last_bar': str(h.index[-1].date())
    }

def tg(method, data=None, files=None):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat: return
    try:
        requests.post(f'https://api.telegram.org/bot{token}/{method}', data={'chat_id': chat, **(data or {})}, files=files, timeout=(15, 90))
    except Exception:
        pass

def send_doc(path, caption):
    try:
        with path.open('rb') as f: tg('sendDocument', {'caption': caption[:900]}, {'document': f})
    except OSError:
        pass

def main():
    started = time.time(); u = universe(); u.to_csv(OUT / 'universe.csv', index=False)
    symbols = u.symbol.tolist(); batches = [symbols[i:i+BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    rows = []; failed = 0
    
    for i, b in enumerate(batches, 1):
        d = download(b)
        if d.empty: failed += 1; continue
        part = u[u.symbol.isin(b)]
        for _, r in part.iterrows():
            h = extract(d, r.symbol, b)
            z = score(r.symbol, r.company, r.exchange, h) if h is not None else None
            if z: rows.append(z)
        if i < len(batches): time.sleep(0.3)
        
    all_df = pd.DataFrame(rows)
    candidate = OUT / 'candidates.csv'; top_path = OUT / 'top10.csv'; report = OUT / 'report.md'
    
    if all_df.empty:
        report.write_text('# Stock Watch — Buffeter Beater\n\nNiciun simbol nu a trecut filtrele stricte.\n', encoding='utf-8')
        tg('sendMessage', {'text': 'Stock Watch: Filtrele stricte n-au returnat simboluri azi.'})
        return 1
        
    all_df = all_df.sort_values(['score', 'avg_dollar_volume_20d'], ascending=[False, False]).reset_index(drop=True)
    all_df.to_csv(candidate, index=False)
    
    top_df = all_df.head(TOP_N)
    top_df.to_csv(top_path, index=False)
    
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    coverage = 100 * len(rows) / len(u) if len(u) else 0
    
    lines = [
        '# 🚀 Stock Watch — Warren Buffett Beater Edition', '',
        f'Generat la: {stamp}', '',
        f'- Univers eligibil analizat: **{len(u):,}** acțiuni',
        f'- Acțiuni trecute prin filtrul instituțional de lichiditate și istoric: **{len(rows):,}**',
        f'- Acoperire date Yahoo Finance: **{coverage:.1f}%**', '',
        '## 🏆 Top 10 Oportunități de Creștere', '',
        '|#|Ticker|Companie|Exch|Preț $|Scor|Seton|RSI|Gap%|Vol Rel|3L%|6L%|12L%|',
        '|---:|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|'
    ]
    
    for i, (_, r) in enumerate(top_df.iterrows(), 1):
        lines.append(f"|{i}|{r.ticker}|{str(r.company)[:20].replace('|','')}|{r.exchange}|{r.price_usd:.2f}|{r.score:.1f}|{r.strategy}|{r.rsi14:.1f}|{r.gap_pct:.2f}|{r.relative_volume:.2f}|{r.return_3m_pct:.1f}|{r.return_6m_pct:.1f}|{r.return_12m_pct:.1f}|")
        
    report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    
    summary = [f'🔥 Buffett Beater — Top {len(top_df)} | {stamp}']
    for i, (_, r) in enumerate(top_df.iterrows(), 1):
        summary.append(f'{i}. {r.ticker} (${r.price_usd:.2f}) — Scor: {r.score:.1f} ({r.strategy})')
    
    tg('sendMessage', {'text': '\n'.join(summary)[:4000]})
    send_doc(report, 'Raport complet Alpha')
    send_doc(top_path, 'Top 10 Selecții de Cursă Lungă')
    return 0

if __name__ == '__main__': raise SystemExit(main())