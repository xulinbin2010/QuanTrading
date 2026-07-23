"""股票池在线源与离线回退的确定性测试（不访问网络）。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import universe


class _Resp:
    def __init__(self, payload=None, text=''):
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class UniverseSourceTests(unittest.TestCase):
    def test_nasdaq_official_constituents_parse_and_persist(self):
        payload = {
            'data': {
                'date': 'Jul 23, 2026 9:30 AM',
                'data': {
                    'rows': [{'symbol': f'X{i:03d}'} for i in range(100)]
                            + [{'symbol': 'ALAB'}, {'symbol': 'CRWV'}, {'symbol': 'ALAB'}],
                },
            },
        }
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(universe, '_UNIVERSE_CACHE_DIR', td),
            patch.object(universe.requests, 'get', return_value=_Resp(payload)),
        ):
            result = universe._try_nasdaq100_official()

        self.assertEqual(102, len(result))
        self.assertIn('ALAB', result)
        self.assertIn('CRWV', result)
        self.assertEqual('nasdaq_official', universe.get_nasdaq100_source_meta()['source'])
        self.assertEqual('Jul 23, 2026 9:30 AM', universe.get_nasdaq100_source_meta()['as_of'])

    def test_nasdaq_rejects_implausibly_short_official_response(self):
        payload = {'data': {'data': {'rows': [{'symbol': 'ALAB'}]}}}
        with patch.object(universe.requests, 'get', return_value=_Resp(payload)):
            self.assertEqual([], universe._try_nasdaq100_official())

    def test_vtwo_paginates_and_deduplicates(self):
        def entities(start: int, count: int):
            return [{'ticker': f'X{i:04d}'} for i in range(start, start + count)]

        pages = [
            _Resp({'size': 1001, 'fund': {'entity': entities(0, 500)}}),
            _Resp({'size': 1001, 'fund': {'entity': entities(500, 500)}}),
            _Resp({'size': 1001, 'fund': {'entity': entities(1000, 1)}}),
        ]
        with patch.object(universe.requests, 'get', side_effect=pages) as get:
            result = universe._try_vtwo_holdings()

        self.assertEqual(1001, len(result))
        self.assertEqual('X0000', result[0])
        self.assertEqual('X1000', result[-1])
        self.assertEqual(3, get.call_count)

    def test_bot_challenge_html_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'bot'):
            universe._reject_bot_challenge(_Resp(text='<!doctype html><title>challenge</title>'))

    def test_russell_falls_back_to_local_cache(self):
        with (
            patch.object(universe, '_try_vtwo_holdings', return_value=[]),
            patch.object(universe, '_try_iwm_holdings', return_value=[]),
            patch.object(universe, '_load_universe_cache', return_value=['AAA', 'BBB']),
        ):
            self.assertEqual(['AAA', 'BBB'], universe.get_russell2000_tickers())

    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as td, patch.object(universe, '_UNIVERSE_CACHE_DIR', td):
            universe._save_universe_cache('russell2000', ['AAA', 'BBB'])
            self.assertTrue((Path(td) / 'russell2000.json').exists())
            self.assertEqual(['AAA', 'BBB'], universe._load_universe_cache('russell2000'))


if __name__ == '__main__':
    unittest.main()
