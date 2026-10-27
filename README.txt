STOCK WATCH HYBRID v2

INSTALL
Replace root stock-main.py, requirements.txt and test_offline.py.
Replace existing workflow with .github/workflows/stock-main.yaml.
Do not keep duplicate scheduled workflows. Schedule preserved: Monday 13:30 UTC.
Keep existing real fundamentals/dividends/coverage data: supplied CSV files are EMPTY templates.
Add repository Actions secret TWELVE_DATA_API_KEY for fallback. Yahoo needs no API key.
Keep TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. No PAT or webhook required.
Run Actions > Stock Main Hybrid > Run workflow.

SELECTION
1. Fresh Yahoo cache, else fresh Twelve Data cache: avoid repeat network calls.
2. Yahoo history (2 years, actions included, max 2 attempts).
3. Twelve Data time_series (400 daily bars, 8.2 seconds spacing, max 3 attempts).
4. Most recent cached history, at most MAX_STALE_DAYS old (default 7), explicitly stale.
Three consecutive failed Yahoo tickers open its breaker for the rest of that run.
Only Twelve Data key/authentication failure disables that provider for the run.
Cache freshness defaults to 6 hours. Cache namespaces are separate by source and new version.
Cache retention in GitHub is best effort, not permanent storage.
This is window refresh, NOT incremental updates. Yahoo may do multiple internal HTTP calls.
No provider-history stitching; each ticker's entire output price history uses one provider.

PRICES
USD only. Daily Close, split-adjusted, NOT dividend-adjusted.
Yahoo uses auto_adjust=False; Twelve Data adjust=splits.
Today's potentially incomplete bar is always excluded, even after the close.
Sources can differ; these are not exchange-complete live prices or guaranteed total-return series.

DIVIDENDS
Fresh Yahoo history with a dividend column and sufficient coverage is used first.
Yahoo completeness is NOT independently verified. Empty event results can mean zero according
 to the provider, NOT proof that no dividend was paid. Review official company announcements.
Dividend window is (today-365 days, today], ex-dividend dates, not cash payment dates.
No stale Yahoo dividend cache is used to claim a fresh TTM figure.
If Yahoo dividends unavailable: use local CSV only when coverage certifies the whole window.
Otherwise TTM and yield are missing, not zero. No paid dividend endpoint is called.
Local dividends.csv columns: ticker,ex_date,amount_per_share_usd,source
Local dividend_coverage.csv columns: ticker,coverage_start,coverage_end,source
Use YYYY-MM-DD. Normalize dividends to current share basis after splits; review reverse splits.
Coverage is your manual assertion, not verified automatically. Source columns are audit references.

FUNDAMENTALS
Manual official input, not automatically fetched. Keep original column schema.
Use YYYY-MM-DD quarter dates. Same metric and consistent units across periods.
NII is not automatically replaced with EPS or other incompatible company earnings measures.
No dividend coverage ratio is computed because quarter/year bases may differ.
Changes compare the latest two entered periods, not CAGR.

OUTPUT
snapshot.csv has full metrics, individual sources, fetch dates and quality flags.
report.md, dashboard.png, per-ticker histories and available Yahoo dividend events are saved.
Telegram sends image plus report/CSV by default; SEND_FILES=0 sends only the image.
Telegram errors are recorded without logging token URLs; reports remain in artifacts.
No automatic POST retries to avoid duplicate deliveries.

VALIDATION
Syntax and synthetic offline tests checked during preparation; test_offline.py runs in Actions.
Tests cover source fallback, cache-only use, stale-cache cutoff, missing dividends, local
 dividend fallback, Yahoo dividend selection and full synthetic report generation.
Live Yahoo, Twelve Data and Telegram access has NOT been tested with your credentials.
API entitlements, rate limits, provider schema and package updates can change.
Dependencies use version ranges, not a reproducible lockfile.

Preparation environment: live yfinance import was stubbed if unavailable; provider calls were mocked. This does not validate live provider behavior.
