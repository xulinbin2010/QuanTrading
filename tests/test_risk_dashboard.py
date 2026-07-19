import unittest
from unittest.mock import patch

from web.services import risk_svc


def _signal(score: int, label: str = 'test') -> dict:
    return {'available': True, 'score': score, 'label': label}


def _thermometer(vix=0, breadth=0, leadership=0, corr=0) -> dict:
    return {
        'vix_term': _signal(vix, 'VIX'),
        'breadth': _signal(breadth, '广度'),
        'leadership': _signal(leadership, '龙头'),
        'correlation': {
            **_signal(corr, '相关性'),
            'avg_corr': 0.45 if corr == 0 else 0.6,
            'enb_corr': 3.2,
            'n': 5,
            'source': 'ib',
        },
        'updated_at': '2026-07-19 10:00:00',
    }


def _leverage(score=10, level='low') -> dict:
    return {
        'generated_at': '2026-07-19T02:00:00+00:00',
        'is_stale': False,
        'market_data_quality': 'delayed_or_near_real_time',
        'summary': {
            'unwind_score': score,
            'unwind_level': level,
            'dominant_market': 'US',
        },
    }


def _dashboard(thermometer: dict, leverage: dict) -> dict:
    with (
        patch.object(risk_svc, 'get_thermometer', return_value=thermometer),
        patch('web.services.leverage_monitor_svc.get_dashboard', return_value=leverage),
    ):
        return risk_svc.get_dashboard()


class RiskDashboardTest(unittest.TestCase):
    def test_thermometer_excludes_ai_proxy_from_score(self):
        proxy = {
            **_signal(2, '高度同步'),
            'source': 'ai_universe',
            'avg_corr': 0.8,
            'enb_corr': 2.0,
            'n': 50,
        }
        with (
            patch.object(risk_svc, '_vix_term_structure', return_value=_signal(0)),
            patch.object(risk_svc, '_breadth', return_value=_signal(0)),
            patch.object(risk_svc, '_leadership_rs', return_value=_signal(0)),
            patch.object(risk_svc, '_portfolio_correlation', return_value=proxy),
        ):
            result = risk_svc.get_thermometer(force=True)
        self.assertEqual(result['score'], 0)
        self.assertEqual(result['max_score'], 6)
        self.assertEqual(result['level'], 'low')

    def test_single_mid_pillar_is_overall_mid(self):
        result = _dashboard(_thermometer(vix=1), _leverage())
        self.assertEqual(result['pillars']['market']['level'], 'low')
        self.assertEqual(result['pillars']['portfolio']['level'], 'low')
        self.assertEqual(result['overall']['level'], 'low')

        result = _dashboard(_thermometer(vix=2), _leverage())
        self.assertEqual(result['pillars']['market']['level'], 'mid')
        self.assertEqual(result['overall']['level'], 'mid')

    def test_two_mid_pillars_upgrade_to_high_by_resonance(self):
        result = _dashboard(
            _thermometer(vix=2, corr=1),
            _leverage(score=40, level='mid'),
        )
        self.assertEqual(result['pillars']['market']['level'], 'mid')
        self.assertEqual(result['pillars']['portfolio']['level'], 'mid')
        self.assertEqual(result['pillars']['leverage']['level'], 'mid')
        self.assertEqual(result['overall']['level'], 'high')

    def test_high_portfolio_risk_does_not_generate_forced_sell_action(self):
        result = _dashboard(_thermometer(corr=2), _leverage())
        self.assertEqual(result['pillars']['portfolio']['level'], 'high')
        self.assertEqual(result['overall']['level'], 'high')
        self.assertIn('暂停新增同主题仓位', result['advice']['tactical'])
        self.assertFalse(result['automated_action'])

    def test_ai_universe_proxy_does_not_affect_overall_risk(self):
        thermometer = _thermometer(corr=2)
        thermometer['correlation']['source'] = 'ai_universe'
        result = _dashboard(thermometer, _leverage())
        self.assertFalse(result['pillars']['portfolio']['available'])
        self.assertEqual(result['pillars']['portfolio']['level'], 'unknown')
        self.assertEqual(result['overall']['level'], 'low')


if __name__ == '__main__':
    unittest.main()
