# Stock Watch — Python + GitHub Actions

Monitors ARR, DX, REFI, SCM, PFLT, AGNC, ARE, CAG and candidate BDCs MAIN, ARCC, BXSL, TSLX, GBDC, GLAD and TRIN.

## What it does
- Runs manually or every Monday at 13:30 UTC.
- Fetches recent price and trailing 365-day cash dividends through `yfinance` / Yahoo Finance.
- Produces `output/report.md` and `output/snapshot.csv` as a downloadable GitHub Actions artifact.
- Compares the latest two rows entered in `fundamentals.csv` for NII/share, NAV/BV/share, annualized dividend/share and shares outstanding.
- Flags declines and a rough dividend-coverage warning.

## Important limitation
Yahoo Finance does not offer a reliable, standardized series of NII/share and NAV/share for all companies. This starter intentionally does not invent those values: enter them from official quarterly reports in `fundamentals.csv`. For BDCs, use quarterly NII/share. For mortgage REITs (ARR, DX, AGNC), use a consistent company-reported earnings metric and keep it distinct from BDC NII.

The script compares the latest two rows entered; that is not automatically a five-year CAGR. With a populated historical CSV, it can be extended to calculate 3-year/5-year CAGR reliably.

## Setup
1. Create a new GitHub repository, e.g. `stock-watch`.
2. Upload all files and folders, preserving `.github/workflows/stock-watch.yml`.
3. Open **Actions** and enable workflows if GitHub prompts you.
4. Choose **Stock Watch** → **Run workflow** to test it.
5. Open the completed run and download the `stock-watch-report` artifact.
6. The weekly schedule runs on the default branch. GitHub scheduled runs use UTC and may be delayed.

## fundamentals.csv columns
- `ticker`: stock symbol
- `period`: report date `YYYY-MM-DD`
- `nii_per_share`: quarterly NII/share (for BDCs)
- `nav_per_share`: NAV/share or tangible book value/share, as appropriate
- `dividend_per_share_annualized`: regular annualized dividend per share
- `shares_outstanding_m`: shares outstanding in millions

Add at least two dated rows per ticker to enable period-to-period comparisons. Use consistent metric definitions for each ticker. Example row values must come from actual filings; do not use guesses.

## Notifications
This first version produces a report artifact but does not send email or Telegram messages yet. Those can be added after the first successful run using GitHub repository Secrets; never put passwords or bot tokens directly in code.
