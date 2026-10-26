import os
import requests
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output'
OUT.mkdir(exist_ok=True)
TICKERS = ['ARR','DX','REFI','SCM','PFLT','AGNC','ARE','CAG','MAIN','ARCC','BXSL','TSLX','GBDC','GLAD','TRIN']
FUND = ROOT / 'fundamentals.csv'

def pct(old, new):
    if pd.isna(old) or pd.isna(new) or old == 0: return None
    return (new / old - 1) * 100

def fmt(v, n=2):
    return 'n/a' if v is None or pd.isna(v) else f'{v:.{n}f}'

def send_telegram_file(file_path, caption, method="sendDocument", field="document"):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        raise RuntimeError(
            "Lipsesc TELEGRAM_BOT_TOKEN sau TELEGRAM_CHAT_ID. "
            "Verifica GitHub Secrets si blocul env din workflow."
        )

    url = f"https://api.telegram.org/bot{token}/{method}"

    try:
        with open(file_path, "rb") as fh:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={field: fh},
                timeout=(15, 90),
            )
    except requests.RequestException:
        # Nu afisam exceptia originala: URL-ul contine tokenul.
        raise RuntimeError("Conexiunea cu Telegram a esuat sau a expirat.") from None

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            f"Telegram a returnat un raspuns invalid (HTTP {response.status_code})."
        ) from None

    if not response.ok or not result.get("ok"):
        description = str(result.get("description", "Eroare necunoscuta")).replace(token, "[REDACTED]")
        raise RuntimeError(
            f"Trimiterea Telegram a esuat (HTTP {response.status_code}): {description}"
        )

    print(f"Trimis pe Telegram: {Path(file_path).name}")


def make_dashboard(snap, path):
    """Genereaza o imagine PNG: grafic cu randamentul + tabel colorat."""
    df = pd.DataFrame(snap)
    df = df.sort_values("yield_ttm_pct", ascending=False, na_position="last").reset_index(drop=True)
    n = len(df)

    bg, fg, grid = "#0f1720", "#e8edf2", "#2a3644"
    fig = plt.figure(figsize=(12, 4.2 + 0.42 * n), facecolor=bg)
    gs = fig.add_gridspec(2, 1, height_ratios=[n * 0.42 + 0.8, n * 0.42 + 1.2], hspace=0.25)

    # --- grafic: randament TTM ---
    ax = fig.add_subplot(gs[0])
    ax.set_facecolor(bg)
    ys = list(range(n))[::-1]
    vals = df["yield_ttm_pct"].fillna(0).tolist()
    colors = ["#e5534b" if v >= 15 else "#d9a441" if v >= 10 else "#3fb27f" for v in vals]
    ax.barh(ys, vals, color=colors, height=0.62)
    ax.set_yticks(ys)
    ax.set_yticklabels(df["ticker"], color=fg, fontsize=11, fontweight="bold")
    for y, v in zip(ys, vals):
        ax.text(v + 0.3, y, f"{v:.1f}%", va="center", color=fg, fontsize=10)
    ax.set_xlim(0, max(vals + [1]) * 1.15)
    ax.tick_params(axis="x", colors=fg)
    ax.xaxis.grid(True, color=grid, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Randament dividende TTM (%)  |  verde <10%   galben 10-15%   rosu >=15%",
                 color=fg, fontsize=12, loc="left", pad=10)

    # --- tabel ---
    ax2 = fig.add_subplot(gs[1])
    ax2.axis("off")
    ax2.set_facecolor(bg)
    header = ["Ticker", "Pret $", "Div TTM $", "Randament %", "Perioada", "Semnale"]
    rows = []
    for _, r in df.iterrows():
        flags = str(r["flags"]).replace("Add quarterly data to fundamentals.csv", "lipsesc date fundamentale")
        rows.append([
            r["ticker"],
            fmt(r["price_usd"]),
            fmt(r["dividends_ttm_per_share_usd"]),
            fmt(r["yield_ttm_pct"], 1),
            str(r["latest_period"]),
            flags[:60],
        ])
    tbl = ax2.table(cellText=rows, colLabels=header, loc="upper center", cellLoc="center",
                    colWidths=[0.09, 0.10, 0.11, 0.13, 0.12, 0.45])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.55)
    for (i, j), cell in tbl.get_celld().items():
        cell.set_edgecolor(grid)
        if i == 0:
            cell.set_facecolor("#1d2a38")
            cell.get_text().set_color(fg)
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor(bg if i % 2 else "#141e29")
            cell.get_text().set_color(fg)
            if j == 5:
                cell.get_text().set_ha("left")
                cell._loc = "left"

    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"
    fig.suptitle(f"Stock Watch  -  {stamp}", color=fg, fontsize=15, fontweight="bold", x=0.02, ha="left", y=0.995)
    fig.text(0.02, 0.005,
             "Date Yahoo Finance, pot fi intarziate. Semnalele sunt alerte de screening, nu recomandari de investitii.",
             color="#8b98a5", fontsize=8)
    fig.savefig(path, dpi=130, facecolor=bg, bbox_inches="tight")
    plt.close(fig)


def main():
    fdf = pd.read_csv(FUND, comment='#')
    for c in ['nii_per_share','nav_per_share','dividend_per_share_annualized','shares_outstanding_m']:
        fdf[c] = pd.to_numeric(fdf[c], errors='coerce')
    lines = ['# Stock Watch Report', '', f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", '',
      '> Price/dividend data come from Yahoo Finance and may be delayed or incomplete.',
      '> Fundamental metrics must be entered from official company reports in fundamentals.csv.',
      '> Changes compare the latest two entered rows, not necessarily a 5-year CAGR.', '',
      '|Ticker|Price $|Dividends TTM/share $|Yield TTM %|Latest period|NII/share|NAV/BV/share|Annual dividend/share|Shares M|NII change|NAV change|Dividend change|Shares change|Flags|',
      '|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    snap=[]
    for t in TICKERS:
        price=divs=yieldp=None; err=''
        try:
            obj=yf.Ticker(t)
            h=obj.history(period='5d')
            if not h.empty: price=float(h['Close'].dropna().iloc[-1])
            ds=obj.dividends
            if ds is not None and not ds.empty:
                ds=ds[ds.index >= (pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=365))]
                divs=float(ds.sum())
            else: divs=0.0
            if price and price>0: yieldp=divs/price*100
        except Exception as e: err=str(e)[:80]
        rows=fdf[fdf.ticker.astype(str).str.upper()==t].copy()
        if not rows.empty:
            rows['sort_date']=pd.to_datetime(rows['period'], errors='coerce')
            rows=rows.sort_values('sort_date'); latest=rows.iloc[-1]; prev=rows.iloc[-2] if len(rows)>1 else None
            period=str(latest['period']); nii=latest['nii_per_share']; nav=latest['nav_per_share']; annual=latest['dividend_per_share_annualized']; shares=latest['shares_outstanding_m']
            nc=pct(prev['nii_per_share'],nii) if prev is not None else None
            vc=pct(prev['nav_per_share'],nav) if prev is not None else None
            dc=pct(prev['dividend_per_share_annualized'],annual) if prev is not None else None
            sc=pct(prev['shares_outstanding_m'],shares) if prev is not None else None
            flags=[]
            if prev is not None and pd.notna(nii) and pd.notna(prev['nii_per_share']) and nii<prev['nii_per_share']: flags.append('NII/share down')
            if prev is not None and pd.notna(nav) and pd.notna(prev['nav_per_share']) and nav<prev['nav_per_share']: flags.append('NAV/BV per share down')
            if prev is not None and pd.notna(annual) and pd.notna(prev['dividend_per_share_annualized']) and annual<prev['dividend_per_share_annualized']: flags.append('Dividend reduced')
            if pd.notna(nii) and pd.notna(annual) and annual>nii*4: flags.append('Dividend > 4x latest quarterly NII (rough check)')
            if not flags: flags.append('Check official filing')
        else:
            period='no data'; nii=nav=annual=shares=nc=vc=dc=sc=None; flags=['Add quarterly data to fundamentals.csv']
        if err: flags.append('Market data error: '+err)
        lines.append(f"|{t}|{fmt(price)}|{fmt(divs)}|{fmt(yieldp,1)}|{period}|{fmt(nii,3)}|{fmt(nav)}|{fmt(annual)}|{fmt(shares)}|{fmt(nc,1) if nc is not None else 'n/a'}%|{fmt(vc,1) if vc is not None else 'n/a'}%|{fmt(dc,1) if dc is not None else 'n/a'}%|{fmt(sc,1) if sc is not None else 'n/a'}%|{'; '.join(flags)}|")
        snap.append({'ticker':t,'price_usd':price,'dividends_ttm_per_share_usd':divs,'yield_ttm_pct':yieldp,'latest_period':period,'nii_per_share':nii,'nav_bv_per_share':nav,'annualized_dividend_per_share':annual,'shares_outstanding_m':shares,'nii_change_pct':nc,'nav_change_pct':vc,'dividend_change_pct':dc,'shares_change_pct':sc,'flags':' | '.join(flags)})
    lines += [
        "",
        "*Yield is trailing cash dividends over the last 365 days "
        "divided by the latest price; it is not a guaranteed forward yield.",
        "",
        "## Important limitations",
        "- Yahoo Finance does not provide standardized NII/share "
        "and NAV/share history for all these companies.",
        "- For BDCs, enter quarterly NII/share. For mortgage REITs "
        "(ARR, DX, AGNC), enter a consistent company-reported earnings "
        "measure and note that it is not directly comparable with BDC NII.",
        "- The script compares the latest two rows only. Add rows "
        "from official reports to build history and calculate true "
        "multi-year CAGR.",
        "- Flags are screening alerts, not buy/sell recommendations.",
    ]

    report_path = OUT / "report.md"
    snapshot_path = OUT / "snapshot.csv"

    report_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    pd.DataFrame(snap).to_csv(
        snapshot_path,
        index=False,
    )

    print("\n".join(lines))

    image_path = OUT / "dashboard.png"
    make_dashboard(snap, image_path)

    # Imaginea apare direct in conversatie, fara sa o deschizi
    send_telegram_file(image_path, "Stock Watch - raport grafic",
                       method="sendPhoto", field="photo")

    # Fisierele .md / .csv raman salvate in output/; le trimit doar daca vrei
    if os.environ.get("SEND_FILES", "").strip() == "1":
        send_telegram_file(report_path, "Stock Watch - raport complet")
        send_telegram_file(snapshot_path, "Stock Watch - date pentru Excel")


if __name__ == "__main__":
    main()
