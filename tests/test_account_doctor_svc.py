from __future__ import annotations

import unittest

from web.services.account_doctor_svc import diagnose


class AccountDoctorServiceTest(unittest.TestCase):
    def test_known_daily_leverage_is_inferred_without_manual_factor(self):
        result = diagnose({
            "account": {"net_liq": 10_000},
            "positions": [
                {"symbol": "MU", "market_value_usd": 4_000},
                {"symbol": "MUU", "market_value_usd": 2_000},
                {"symbol": "RAM", "market_value_usd": 1_000},
            ],
        }, persist=False)

        positions = {row["symbol"]: row for row in result["positions"]}
        self.assertEqual(positions["MU"]["leverage_factor"], 1.0)
        self.assertEqual(positions["MUU"]["leverage_factor"], 2.0)
        self.assertEqual(positions["RAM"]["leverage_factor"], 2.0)
        self.assertEqual(result["account"]["total_exposure"], 10_000)


if __name__ == "__main__":
    unittest.main()
