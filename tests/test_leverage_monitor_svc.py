from __future__ import annotations

import unittest

import pandas as pd

from web.services.leverage_monitor_svc import (
    LeveragedProduct,
    _market_aggregate,
    _parse_finra_html,
    _parse_korea_margin_items,
    _product_row,
    _tracking_metrics,
)


def _price_frame(daily_return: float, periods: int = 35, volume: float = 1_000_000) -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=periods, freq="B")
    close = pd.Series(
        [100 * (1 + daily_return) ** i for i in range(periods)],
        index=index,
    )
    return pd.DataFrame({
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    })


class LeverageMonitorServiceTest(unittest.TestCase):
    def test_tracking_drag_uses_daily_compounded_target(self):
        benchmark = _price_frame(0.01)
        product = _price_frame(0.02)

        gap_1d, drag_20d = _tracking_metrics(product, benchmark, leverage=2.0)

        self.assertAlmostEqual(gap_1d or 0, 0.0, places=12)
        self.assertAlmostEqual(drag_20d or 0, 0.0, places=12)

    def test_product_row_contains_market_and_tracking_metrics(self):
        product = LeveragedProduct(
            symbol="TEST2X",
            name="Test 2x",
            market="US",
            leverage=2.0,
            benchmark="BASE",
            theme="test",
            provider="test",
        )
        prices = {
            "TEST2X": _price_frame(0.02),
            "BASE": _price_frame(0.01),
        }

        row = _product_row(product, prices)

        self.assertTrue(row["available"])
        self.assertEqual(row["direction"], "long")
        self.assertAlmostEqual(row["ret_1d"], 0.02, places=10)
        self.assertAlmostEqual(row["volume_ratio"], 1.0, places=10)
        self.assertAlmostEqual(row["tracking_gap_1d"], 0.0, places=10)
        self.assertAlmostEqual(row["tracking_drag_20d"], 0.0, places=10)

    def test_market_aggregate_reaches_full_unwind_score_when_all_signals_max(self):
        rows = [
            {
                "available": True,
                "direction": "long",
                "latest_date": "2026-07-17",
                "ret_1d": -0.06,
                "volume_ratio": 3.0,
                "tracking_gap_1d": -0.02,
                "dollar_volume": 1_000_000,
            },
            {
                "available": True,
                "direction": "inverse",
                "latest_date": "2026-07-17",
                "ret_1d": 0.06,
                "volume_ratio": 3.0,
                "tracking_gap_1d": 0.02,
                "dollar_volume": 1_000_000,
            },
        ]
        funding = {"available": True, "mom": -0.05}

        result = _market_aggregate(rows, funding)

        self.assertEqual(result["unwind_score"], 100.0)
        self.assertEqual(result["unwind_level"], "high")
        self.assertEqual(result["as_of"], "2026-07-17")
        self.assertEqual(result["older_bar_count"], 0)
        self.assertEqual(sum(c["max_points"] for c in result["score_components"]), 100)

    def test_parse_finra_html_normalizes_and_sorts_months(self):
        html = """
        <table>
          <thead>
            <tr>
              <th>Month/Year</th>
              <th>Debit Balances in Customers' Securities Margin Accounts</th>
              <th>Free Credit Balances in Customers' Cash Accounts</th>
              <th>Free Credit Balances in Customers' Securities Margin Accounts</th>
            </tr>
          </thead>
          <tbody>
            <tr><td>Jun-26</td><td>1,502,072</td><td>217,441</td><td>223,412</td></tr>
            <tr><td>May-26</td><td>1,415,557</td><td>206,600</td><td>217,256</td></tr>
          </tbody>
        </table>
        """

        rows = _parse_finra_html(html)

        self.assertEqual([r["date"] for r in rows], ["2026-05-01", "2026-06-01"])
        self.assertEqual(rows[-1]["debit_usd_m"], 1_502_072)
        self.assertEqual(rows[-1]["margin_credit_usd_m"], 223_412)

    def test_parse_korea_margin_items_accepts_common_official_field_names(self):
        items = [
            {"basDt": "20260716", "crdtLoanBal": "21,500", "kospi": "12,000", "kosdaq": "9,500"},
            {"basDt": "20260717", "crdtLoanBal": "21,750", "kospi": "12,100", "kosdaq": "9,650"},
        ]

        rows = _parse_korea_margin_items(items)

        self.assertEqual(rows[-1], {
            "date": "2026-07-17",
            "credit_krw_100m": 21_750,
            "kospi_krw_100m": 12_100,
            "kosdaq_krw_100m": 9_650,
        })


if __name__ == "__main__":
    unittest.main()
