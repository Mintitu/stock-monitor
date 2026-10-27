import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

spec = importlib.util.spec_from_file_location("stock_main", Path(__file__).with_name("stock-main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample(age=0):
    return {"fetched_at": time.time() - age, "currency": "USD", "bars": [{"date": "2026-10-20", "close": 10.0}], "events": [], "dividends_available": False}


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(m, ROOT=self.root, OUT=self.root / "output", CACHE=self.root / "cache")
        self.paths.start()
        m.CACHE.mkdir()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_yahoo_primary(self):
        with patch.object(m, "yahoo_fetch", return_value=sample()), patch.object(m, "td_fetch") as td:
            x, source, y, flags = m.choose_prices("TEST")
            self.assertEqual(source, "Yahoo")
            td.assert_not_called()

    def test_fallback(self):
        with patch.object(m, "yahoo_fetch", side_effect=RuntimeError()), patch.object(m, "td_fetch", return_value=sample()):
            self.assertEqual(m.choose_prices("TEST")[1], "Twelve Data")

    def test_fresh_cache_no_network(self):
        m.cache_save("TEST", "yahoo", sample())
        with patch.object(m, "yahoo_fetch") as y, patch.object(m, "td_fetch") as t:
            self.assertEqual(m.choose_prices("TEST")[1], "Yahoo cache")
            y.assert_not_called()
            t.assert_not_called()

    def test_stale_cache(self):
        m.cache_save("TEST", "yahoo", sample(86400))
        with patch.object(m, "yahoo_fetch", side_effect=RuntimeError()), patch.object(m, "td_fetch", side_effect=RuntimeError()):
            self.assertEqual(m.choose_prices("TEST")[1], "Yahoo stale cache")

    def test_expired_cache(self):
        m.cache_save("TEST", "yahoo", sample(10 * 86400))
        with patch.object(m, "yahoo_fetch", side_effect=RuntimeError()), patch.object(m, "td_fetch", side_effect=RuntimeError()):
            self.assertIsNone(m.choose_prices("TEST")[0])

    def test_dividends(self):
        today = pd.Timestamp("2026-10-27", tz="UTC")
        cutoff = today - pd.Timedelta(days=365)
        events = pd.DataFrame(columns=["ticker", "date", "amount"])
        cov = pd.DataFrame(columns=["ticker", "start", "end"])
        self.assertIsNone(m.dividend_value("TEST", None, today, cutoff, events, cov)[0])
        events = pd.DataFrame([{"ticker": "TEST", "date": today - pd.Timedelta(days=10), "amount": 1.0}])
        cov = pd.DataFrame([{"ticker": "TEST", "start": cutoff, "end": today}])
        self.assertEqual(m.dividend_value("TEST", None, today, cutoff, events, cov)[0], 1.0)
        y = sample()
        y.update(dividends_available=True, coverage_start="2024-10-27", coverage_end="2026-10-26", events=[{"date": "2026-10-01", "amount": 2.0}])
        self.assertEqual(m.dividend_value("TEST", y, today, cutoff, events, cov)[0], 2.0)
        y["fetched_at"] -= 86400
        self.assertEqual(m.dividend_value("TEST", y, today, cutoff, events, cov)[0], 1.0)

    def test_full_report(self):
        y = sample()
        with patch.object(m, "TICKERS", ["TEST"]), patch.object(m, "choose_prices", return_value=(y, "synthetic", None, [])), patch.object(m, "send_file", return_value="mock: no network"):
            m.main()
        df = pd.read_csv(m.OUT / "snapshot.csv")
        self.assertEqual(df.price_usd.iloc[0], 10)
        self.assertTrue(pd.isna(df.yield_ttm_pct.iloc[0]))
        self.assertTrue((m.OUT / "report.md").exists())
        self.assertTrue((m.OUT / "dashboard.png").stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
