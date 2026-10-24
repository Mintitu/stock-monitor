# Stock Watch — Nasdaq + NYSE scanner

## Included files
- `stock-all.py`: downloads the official Nasdaq Trader symbol directories, filters the eligible Nasdaq and NYSE universe, downloads up to 3 years of daily Yahoo Finance bars, calculates technical indicators, ranks setups and sends a report to Telegram.
- `.github/workflows/stock-all.yml`: manual and weekly scanner workflow.
- `telegram_poll.py` and `.github/workflows/telegram-command.yml`: checks Telegram every 5 minutes for `/raport` and dispatches the scanner.
- `requirements.txt`: Python dependencies.

## GitHub secrets
Keep these repository secrets configured: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `GH_PAT`. The fine-grained `GH_PAT` needs **Actions: Read and write** access to this repository. Never put tokens in source files.

## Install
Copy the files into the repository at the same paths, commit to the default branch, then run **Stock Watch Scanner** manually from Actions once. After that, send `/raport` to the Telegram bot. GitHub's scheduled poller runs every five minutes, so the command is not instant.

## Filters and scoring
- Nasdaq-listed issues from `nasdaqlisted.txt` and NYSE-listed issues where `otherlisted.txt` has `Exchange == N`.
- Excludes ETF-flagged other-listed securities, test issues, securities with non-normal Nasdaq financial status, and common fund/warrant/unit/right/preferred/fixed-income name/suffix patterns. Nasdaq's file does not expose the same ETF flag as the other-listed file, so name-based Nasdaq exclusions are not perfect.
- Price >= $5, 20-day average dollar volume >= $5 million, and at least 600 daily bars.
- Three-year history; RSI(14), gap, relative volume, EMA20, SMA50/200, MACD, ATR and 3/6/12-month returns.
- Blend is 55% trend score + 45% rebound score; selection aims for up to four trend and three rebound setups, then fills remaining slots by blended score.

## Important limitations
Yahoo Finance can throttle or omit symbols. The report includes the actual universe size, symbols scored, failed batches, and coverage percentage. Coverage below 100% means the run did not rank the entire universe. This first version is a technical screen, not a fundamental/news analysis or backtested strategy, and its scores are not guaranteed returns.
