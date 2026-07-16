"""AI 股票池吐故纳新中的汰旧逻辑测试（只建议，不自动移除）。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from web.services import ai_tracker_svc


def _prices() -> dict[str, pd.DataFrame]:
    idx = pd.bdate_range('2025-01-02', periods=170)
    return {
        'SPY': pd.DataFrame({'close': np.linspace(100, 150, len(idx))}, index=idx),
        'WEAK': pd.DataFrame({'close': np.linspace(100, 35, len(idx))}, index=idx),
        'HIDDEN': pd.DataFrame({'close': np.linspace(100, 30, len(idx))}, index=idx),
        'A': pd.DataFrame({'close': np.linspace(100, 180, len(idx))}, index=idx),
        'B': pd.DataFrame({'close': np.linspace(100, 190, len(idx))}, index=idx),
        'C': pd.DataFrame({'close': np.linspace(100, 200, len(idx))}, index=idx),
        'D': pd.DataFrame({'close': np.linspace(100, 210, len(idx))}, index=idx),
    }


class _Store:
    def get(self, symbols, start=None, auto_update=True):
        data = _prices()
        return {s: data[s] for s in symbols if s in data}


class AITrackerMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.universe = {
            'groups': {
                'visible': {'label': '可见池', 'symbols': ['WEAK', 'A', 'B', 'C', 'D']},
                'hidden': {'label': '收藏', 'symbols': ['HIDDEN'], 'hidden': True},
            },
            'trade_priority': {'WEAK': False},
            'retire_keep': {},
        }

    def test_suggest_retire_only_returns_persistently_weak_visible_symbol(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(ai_tracker_svc, 'load_universe', return_value=self.universe), \
             patch('core.data_store.DataStore', return_value=_Store()), \
             patch.object(ai_tracker_svc, '_AI_RETIRE_CACHE', Path(td) / 'retire.json'):
            result = ai_tracker_svc.suggest_retire()

        self.assertEqual(['WEAK'], [x['symbol'] for x in result['suggestions']])
        self.assertFalse(result['suggestions'][0]['trade_priority'])
        self.assertNotIn('HIDDEN', [x['symbol'] for x in result['suggestions']])
        self.assertEqual(['WEAK', 'A', 'B', 'C', 'D'], self.universe['groups']['visible']['symbols'])

    def test_recent_keep_suppresses_suggestion(self):
        kept = {**self.universe, 'retire_keep': {'WEAK': pd.Timestamp.today().date().isoformat()}}
        with tempfile.TemporaryDirectory() as td, \
             patch.object(ai_tracker_svc, 'load_universe', return_value=kept), \
             patch('core.data_store.DataStore', return_value=_Store()), \
             patch.object(ai_tracker_svc, '_AI_RETIRE_CACHE', Path(td) / 'retire.json'):
            result = ai_tracker_svc.suggest_retire()

        self.assertEqual([], result['suggestions'])


if __name__ == '__main__':
    unittest.main()
