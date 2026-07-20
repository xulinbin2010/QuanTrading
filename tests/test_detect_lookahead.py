import unittest

from scripts.detect_lookahead import CRITICAL, WARNING, _regex_issues


class DetectLookaheadRegexTests(unittest.TestCase):
    def test_equity_summary_last_value_is_allowed(self):
        source = """
ret = eq.iloc[-1] / initial_cash - 1.0
final_equity = float(equity_series.iloc[-1])
"""

        self.assertEqual(_regex_issues(source, "strategy.py"), [])

    def test_signal_dataframe_last_value_is_still_critical(self):
        issues = _regex_issues(
            "signal = df.iloc[-1]['close'] > df.iloc[-1]['ema']",
            "strategy.py",
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].level, CRITICAL)

    def test_string_join_is_not_dataframe_join(self):
        issues = _regex_issues(
            """return f"DynamicFactor({', '.join(enabled_factors)})" """,
            "strategy.py",
        )

        self.assertEqual(issues, [])

    def test_dataframe_join_remains_warning(self):
        issues = _regex_issues(
            "combined = prices.join(fundamentals)",
            "strategy.py",
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].level, WARNING)


if __name__ == "__main__":
    unittest.main()
