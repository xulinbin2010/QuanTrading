"""A 股基本面研究服务的纯函数与状态契约测试。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from web.services import astock_fundamental_svc as svc


def _record(period: str, revenue: float, profit: float, **extra) -> dict:
    parts = svc._period_parts(period)
    return {
        'report_period': period,
        'report_label': svc._period_label(period),
        'report_year': parts[0],
        'report_quarter': parts[1],
        'is_cumulative': True,
        'revenue_yi': revenue,
        'revenue_yoy': None,
        'net_profit_yi': profit,
        'net_profit_yoy': None,
        'eps': None,
        'roe': None,
        'gross_margin': None,
        'announcement_date': None,
        **extra,
    }


class AStockFundamentalServiceTests(unittest.TestCase):
    def test_candidate_periods_are_completed_and_newest_first(self):
        periods = svc._candidate_report_periods(pd.Timestamp('2026-08-05').date(), limit=5)
        self.assertEqual(
            periods,
            ['20260630', '20260331', '20251231', '20250930', '20250630'],
        )

    def test_normalize_report_row_converts_yuan_and_percent_points(self):
        raw = pd.Series({
            '营业总收入-营业总收入': 12_345_678_900,
            '营业总收入-同比增长': 15.2,
            '净利润-净利润': -987_654_300,
            '净利润-同比增长': -20.0,
            '每股收益': -0.18,
            '净资产收益率': -3.5,
            '销售毛利率': 31.4,
            '最新公告日期': '2026-07-31',
        })
        row = svc._normalize_report_row(raw, '20260630')
        self.assertAlmostEqual(row['revenue_yi'], 123.4568)
        self.assertAlmostEqual(row['revenue_yoy'], 0.152)
        self.assertAlmostEqual(row['net_profit_yi'], -9.8765)
        self.assertAlmostEqual(row['net_profit_yoy'], -0.2)
        self.assertAlmostEqual(row['roe'], -0.035)
        self.assertEqual(row['announcement_date'], '2026-07-31')

    def test_yjbb_adapter_filters_theme_codes_and_maps_eastmoney_fields(self):
        response = MagicMock()
        response.json.return_value = {
            'result': {
                'pages': 1,
                'data': [{
                    'SECURITY_CODE': '000001',
                    'BASIC_EPS': 0.25,
                    'TOTAL_OPERATE_INCOME': 100_000_000,
                    'YSTZ': 12.5,
                    'PARENT_NETPROFIT': 20_000_000,
                    'SJLTZ': 8.0,
                    'WEIGHTAVG_ROE': 4.2,
                    'XSMLL': 25.0,
                    'NOTICE_DATE': '2026-07-31 00:00:00',
                }],
            }
        }
        with patch.object(svc.requests, 'get', return_value=response) as get:
            frame = svc._fetch_yjbb_period('20260630', ['000001', '600519'])
        params = get.call_args.kwargs['params']
        self.assertIn("SECURITY_CODE in (000001,600519)", params['filter'])
        self.assertEqual(frame.loc[0, '股票代码'], '000001')
        self.assertEqual(frame.loc[0, '净利润-净利润'], 20_000_000)

    def test_tencent_fallback_maps_quote_fields_and_units(self):
        normalized = svc._normalize_tencent_valuation_row({
            'code': 'sz000021',
            'zxj': '38.70',
            'zsz': '609.28',
            'ltsz': '609.20',
            'pe_ttm': '50.82',
            'pn': '4.68',
            'zdf': '5.91',
        })
        self.assertIsNotNone(normalized)
        code, row = normalized
        self.assertEqual(code, '000021')
        self.assertEqual(row['price'], 38.70)
        self.assertEqual(row['total_market_cap_yi'], 609.28)
        self.assertEqual(row['float_market_cap_yi'], 609.20)
        self.assertEqual(row['pe_dynamic'], 50.82)
        self.assertEqual(row['pb'], 4.68)
        self.assertAlmostEqual(row['quote_change_pct'], 0.0591)

    def test_valuation_fallback_fills_missing_eastmoney_fields(self):
        from core.astock_data_store import ak

        with patch.object(ak, 'stock_zh_a_spot_em', side_effect=RuntimeError('upstream closed')), patch.object(
            svc, '_fetch_tencent_valuation', return_value=(
                {'000021': {
                    'price': 38.70,
                    'total_market_cap_yi': 609.28,
                    'float_market_cap_yi': 609.20,
                    'pe_dynamic': 50.82,
                    'pb': 4.68,
                    'quote_change_pct': 0.0591,
                }},
                {'status': 'ok', 'coverage': 1, 'total': 1},
            )
        ):
            rows, state = svc._fetch_valuation(['000021'])
        self.assertEqual(rows['000021']['price'], 38.70)
        self.assertEqual(rows['000021']['total_market_cap_yi'], 609.28)
        self.assertEqual(rows['000021']['pe_dynamic'], 50.82)
        self.assertEqual(rows['000021']['pb'], 4.68)
        self.assertTrue(state['fallback_used'])
        self.assertEqual(state['name'], 'tencent_spot')

    def test_single_quarter_is_derived_from_adjacent_cumulative_period(self):
        records = [
            _record('20260630', 180.0, 24.0),
            _record('20260331', 100.0, 10.0),
        ]
        current = records[0]
        revenue, profit, derivable = svc._derive_single_quarter(records, current)
        self.assertTrue(derivable)
        self.assertEqual(revenue, 80.0)
        self.assertEqual(profit, 14.0)

    def test_single_quarter_stays_null_when_adjacent_report_is_missing(self):
        current = _record('20260630', 180.0, 24.0)
        revenue, profit, derivable = svc._derive_single_quarter([current], current)
        self.assertFalse(derivable)
        self.assertIsNone(revenue)
        self.assertIsNone(profit)

    def test_ttm_uses_current_cumulative_plus_prior_fy_minus_prior_same_period(self):
        records = [
            _record('20260630', 180.0, 24.0),
            _record('20260331', 100.0, 10.0),
            _record('20251231', 400.0, 60.0),
            _record('20250630', 150.0, 20.0),
        ]
        revenue, profit, state = svc._calculate_ttm(records)
        self.assertEqual(revenue, 430.0)
        self.assertEqual(profit, 64.0)
        self.assertEqual(state, 'valid')

    def test_ttm_growth_compares_with_same_period_prior_ttm(self):
        records = [
            _record('20260630', 180.0, 24.0),
            _record('20260331', 100.0, 10.0),
            _record('20251231', 400.0, 60.0),
            _record('20250630', 150.0, 20.0),
            _record('20241231', 350.0, 50.0),
            _record('20240630', 130.0, 15.0),
        ]
        revenue_growth, profit_growth, state = svc._ttm_growth(records)
        self.assertAlmostEqual(revenue_growth, 430.0 / 370.0 - 1, places=6)
        self.assertAlmostEqual(profit_growth, 64.0 / 55.0 - 1, places=6)
        self.assertEqual(state, 'valid')

    def test_summary_row_exposes_earnings_yield_and_quality(self):
        row = svc._build_summary_row(
            '000001',
            {'name': '测试股', 'group': 'other', 'group_label': '其他', 'subcat': 'x', 'subcat_label': '测试'},
            {'total_market_cap_yi': 100.0, 'pe_dynamic': 12.0},
            [_record('20251231', 100.0, 10.0, roe=0.20, net_profit_yoy=0.10)],
        )
        self.assertAlmostEqual(row['earnings_yield'], 0.1)
        self.assertEqual(row['quality_status'], 'strong')
        self.assertEqual(row['ttm_growth_state'], 'not_derivable')

    def test_ttm_marks_loss_making_without_returning_a_negative_pe(self):
        records = [_record('20251231', 100.0, -2.0)]
        revenue, profit, state = svc._calculate_ttm(records)
        self.assertEqual(revenue, 100.0)
        self.assertEqual(profit, -2.0)
        self.assertEqual(state, 'loss_making')
        row = svc._build_summary_row(
            '000001',
            {'name': '测试股', 'group': 'other', 'group_label': '其他', 'subcat': 'x', 'subcat_label': '测试'},
            {'total_market_cap_yi': 100.0, 'pe_dynamic': -1.0},
            records,
        )
        self.assertIsNone(row['pe_ttm'])
        self.assertAlmostEqual(row['earnings_yield'], -0.02)
        self.assertEqual(row['pe_state'], 'loss_making')

    def test_public_summary_does_not_expose_private_reconstruction_fields(self):
        public = svc._public_summary({'rows': [], '_financials': {'000001': []}, '_meta': {}})
        self.assertEqual(public, {'rows': []})

    def test_summary_build_uses_independent_valuation_and_financial_status(self):
        valuation = {
            '000001': {
                'price': 10.0,
                'total_market_cap_yi': 100.0,
                'float_market_cap_yi': 90.0,
                'pe_dynamic': 12.0,
                'pb': 1.5,
                'quote_change_pct': 0.01,
            }
        }
        records = {'000001': [_record('20251231', 100.0, 10.0)]}
        with patch.object(svc, '_load_theme_universe', return_value=(
            ['000001'],
            {'000001': {'name': '测试股', 'group': 'other', 'group_label': '其他', 'subcat': 'x', 'subcat_label': '测试'}},
        )), patch.object(svc, '_fetch_valuation', return_value=(valuation, {'status': 'ok', 'coverage': 1, 'total': 1})), patch.object(
            svc, '_fetch_financials', return_value=(records, {'status': 'partial', 'coverage': 1, 'total': 1})
        ):
            result = svc._build_summary()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['coverage'], {'valuation': 1, 'financial': 1})
        self.assertEqual(result['rows'][0]['pe_ttm'], 10.0)
        self.assertEqual(result['rows'][0]['pe_dynamic'], 12.0)


if __name__ == '__main__':
    unittest.main()
