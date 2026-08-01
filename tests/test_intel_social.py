"""情报中心与社区热度的纯逻辑、并发保护和失败降级测试。"""
import threading
import time
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

import config
from core import social_buzz
from core.database import Database
from web.services import intel_svc, social_svc


class IntelSocialTests(unittest.TestCase):
    def test_intel_auto_prefers_codex_subscription(self):
        with (
            patch.dict('os.environ', {'INTEL_ENGINE': 'auto'}, clear=False),
            patch.object(intel_svc, '_codex_path', return_value='/bin/codex'),
            patch.object(intel_svc, '_call_codex_cli', return_value=('ok', {'engine': 'cli/codex/default'})) as codex,
            patch.object(intel_svc, '_call_claude_cli') as claude,
            patch.object(intel_svc, '_call_claude_api') as api,
        ):
            text, usage = intel_svc._call_claude('system', 'user')

        self.assertEqual('ok', text)
        self.assertEqual('cli/codex/default', usage['engine'])
        codex.assert_called_once_with('system', 'user')
        claude.assert_not_called()
        api.assert_not_called()

    def test_intel_auto_falls_back_to_claude_cli_but_never_paid_api(self):
        with (
            patch.dict('os.environ', {
                'INTEL_ENGINE': 'auto',
                'ANTHROPIC_API_KEY': 'configured-but-must-not-auto-spend',
            }, clear=False),
            patch.object(intel_svc, '_codex_path', return_value='/bin/codex'),
            patch.object(intel_svc, '_cli_path', return_value='/bin/claude'),
            patch.object(intel_svc, '_call_codex_cli', side_effect=RuntimeError('not logged in')),
            patch.object(intel_svc, '_call_claude_cli', side_effect=RuntimeError('not logged in')),
            patch.object(intel_svc, '_call_claude_api') as api,
        ):
            with self.assertRaisesRegex(intel_svc.MissingAPIKey, '不会调用 Anthropic API'):
                intel_svc._call_claude('system', 'user')

        api.assert_not_called()

    def test_intel_explicit_api_still_supported(self):
        with (
            patch.dict('os.environ', {'INTEL_ENGINE': 'api'}, clear=False),
            patch.object(intel_svc, '_call_claude_api', return_value=('api', None)) as api,
        ):
            text, _ = intel_svc._call_claude('system', 'user', max_tokens=321)

        self.assertEqual('api', text)
        api.assert_called_once_with('system', 'user', 321)

    def test_parse_events_accepts_markdown_json_and_normalizes(self):
        raw = '''```json
{"events":[{"scope":"候选池","type":"大单合作","symbols":["nvda"],
"direction":"利好","strength":"强","title":"获得订单","analysis":"关注兑现",
"date":"2026-07-16","links":["https://example.com"]}]}
```'''
        events = intel_svc._parse_events_json(raw)
        self.assertEqual(1, len(events))
        self.assertEqual('NVDA', events[0]['symbols'][0])
        self.assertEqual('候选池', events[0]['scope'])

    def test_parse_events_rejects_non_json(self):
        with self.assertRaisesRegex(RuntimeError, '不是 JSON'):
            intel_svc._parse_events_json('no structured result')

    def test_news_refresh_allows_only_one_background_worker(self):
        started = threading.Event()
        release = threading.Event()

        def fake_generate():
            started.set()
            release.wait(timeout=2)
            return {}

        intel_svc._events_running = False
        intel_svc._events_error = None
        with patch.object(intel_svc, 'generate_news_events', side_effect=fake_generate):
            self.assertTrue(intel_svc.refresh_news_events_async())
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(intel_svc.refresh_news_events_async())
            release.set()
            for _ in range(100):
                if not intel_svc.news_events_status()['running']:
                    break
                time.sleep(0.01)
        self.assertFalse(intel_svc.news_events_status()['running'])

    def test_zscore_uses_prior_days_as_baseline(self):
        rows = [
            ('NVDA', '2026-07-13', 10),
            ('NVDA', '2026-07-14', 10),
            ('NVDA', '2026-07-15', 10),
            ('NVDA', '2026-07-16', 40),
        ]
        result = social_svc._zscore_map(rows)
        self.assertEqual(40, result['NVDA']['today'])
        self.assertEqual(10, result['NVDA']['avg7'])
        self.assertGreater(result['NVDA']['z'], 2)

    def test_zscore_does_not_signal_on_weekend(self):
        rows = [
            ('NVDA', '2026-07-15', 20),
            ('NVDA', '2026-07-16', 20),
            ('NVDA', '2026-07-17', 20),
            ('NVDA', '2026-07-19', 1),
        ]
        result = social_svc._zscore_map(rows)
        self.assertEqual(1, result['NVDA']['today'])
        self.assertIsNone(result['NVDA']['z'])
        self.assertEqual('non_trading_day', result['NVDA']['status'])

    def test_stocktwits_sentiment_uses_strict_24h_window(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        old = datetime.now(timezone.utc) - timedelta(days=2)

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {'messages': [
                    {'created_at': recent.strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'entities': {'sentiment': {'basic': 'Bearish'}}},
                    {'created_at': old.strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'entities': {'sentiment': {'basic': 'Bullish'}}},
                ]}

        with (
            patch.object(social_buzz.requests, 'get', return_value=FakeResponse()),
            patch.object(social_buzz.time, 'sleep'),
        ):
            batch = social_buzz.fetch_stocktwits(['NVDA'])

        self.assertEqual('ok', batch['status'])
        self.assertEqual(1, batch['rows'][0]['mentions'])
        self.assertEqual(0, batch['rows'][0]['bull_cnt'])
        self.assertEqual(1, batch['rows'][0]['bear_cnt'])
        self.assertEqual(24, batch['rows'][0]['extra']['window_hours'])

    def test_stocktwits_cloudflare_challenge_stops_remaining_requests(self):
        class ChallengeResponse:
            status_code = 403
            headers = {'cf-mitigated': 'challenge'}

            def raise_for_status(self):
                raise AssertionError('challenge should be handled before raise_for_status')

        with (
            patch.object(social_buzz.requests, 'get', return_value=ChallengeResponse()) as get,
            patch.object(social_buzz.time, 'sleep'),
        ):
            batch = social_buzz.fetch_stocktwits(['NVDA', 'AMD', 'MU'])

        self.assertEqual('unavailable', batch['status'])
        self.assertEqual(0, batch['covered_count'])
        self.assertEqual([], batch['rows'])
        self.assertIn('Cloudflare browser challenge', batch['detail'])
        get.assert_called_once()

    def test_social_monitored_does_not_connect_ib_gateway(self):
        with (
            patch('web.services.ai_tracker_svc.load_universe', return_value={
                'groups': {'gpu': {'symbols': ['NVDA']}}
            }),
            patch('web.services.intel_svc._holdings_for_intel', return_value=[
                {'symbol': 'MUU'}
            ]) as holdings,
            patch('web.services.intel_svc._news_symbols', return_value=['MU']),
        ):
            tags = social_svc._monitored()

        holdings.assert_called_once_with(include_ib=False)
        self.assertEqual({'NVDA': 'AI池', 'MU': '持仓'}, tags)

    def test_apewisdom_retries_transient_timeout(self):
        class GoodResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {'results': [{'ticker': 'NVDA', 'mentions': '12', 'rank': '8'}]}

        with (
            patch.object(social_buzz.requests, 'get', side_effect=[
                requests.Timeout('temporary timeout'), GoodResponse(), GoodResponse(),
            ]) as get,
            patch.object(social_buzz.time, 'sleep'),
        ):
            batch = social_buzz.fetch_apewisdom({'NVDA'}, pages=2)

        self.assertEqual('ok', batch['status'])
        self.assertEqual(2, batch['row_count'])
        self.assertEqual(3, get.call_count)

    def test_sentiment_compares_with_own_strict_window_baseline(self):
        extra = '{"window_hours": 24, "sampled_24h": 30}'
        rows = [
            ('NVDA', '2026-07-13', 30, None, None, 8, 2, '2026-07-13 21:00:00', extra),
            ('NVDA', '2026-07-14', 30, None, None, 8, 2, '2026-07-14 21:00:00', extra),
            ('NVDA', '2026-07-15', 30, None, None, 8, 2, '2026-07-15 21:00:00', extra),
            ('NVDA', '2026-07-16', 30, None, None, 4, 6, '2026-07-16 21:00:00', extra),
        ]
        result = social_svc._sentiment_map(rows)['NVDA']
        self.assertEqual(0.8, result['bull_baseline'])
        self.assertEqual(-40.0, result['bull_delta_pp'])
        self.assertEqual('deteriorating', result['sentiment_shift'])

    def test_collect_degrades_when_all_sources_return_empty(self):
        class FakeDB:
            def connect(self):
                return None

            def add_social_mentions(self, rows):
                self.rows = rows
                return len(rows)

            def prune_social_mentions(self, keep_days=90):
                return 0

            def close(self):
                return None

        with (
            patch.object(social_buzz, 'fetch_apewisdom', return_value=[]),
            patch.object(social_buzz, 'fetch_reddit_posts', return_value=[]),
            patch.object(social_buzz, 'fetch_stocktwits', return_value=[]),
            patch('core.database.Database', return_value=FakeDB()),
        ):
            result = social_buzz.collect({'NVDA'}, ['NVDA'])
        self.assertEqual(0, result['saved'])
        self.assertEqual({
            'apewisdom': 'unavailable',
            'reddit_posts': 'unavailable',
            'stocktwits': 'unavailable',
        }, result['source_status'])

    def test_social_mentions_database_round_trip_and_prune(self):
        with tempfile.TemporaryDirectory() as td, patch.object(config, 'DB_PATH', str(Path(td) / 'test.db')):
            db = Database()
            db.connect()
            saved = db.add_social_mentions([
                {'symbol': 'NVDA', 'source': 'apewisdom', 'trade_date': date.today().isoformat(),
                 'mentions': 42, 'rank': 3, 'extra': {'sample': True}},
                {'symbol': 'NVDA', 'source': 'apewisdom', 'trade_date': date.today().isoformat(),
                 'mentions': 35, 'rank': 5, 'extra': {'sample': 'latest'}},
                {'symbol': 'OLD', 'source': 'apewisdom', 'trade_date': '2000-01-01',
                 'mentions': 1},
            ])
            db.add_social_collection_runs([{
                'source': 'apewisdom', 'trade_date': date.today().isoformat(),
                'status': 'ok', 'requested_count': 80, 'covered_count': 20,
                'row_count': 20, 'detail': 'top 200',
            }])
            rows = db.get_social_daily('apewisdom', days=14)
            runs = db.get_latest_social_collection_runs()
            pruned = db.prune_social_mentions(keep_days=90)
            db.close()

        self.assertEqual(3, saved)
        self.assertEqual('NVDA', rows[0][0])
        self.assertEqual(35, rows[0][2])
        self.assertEqual('ok', runs[0][2])
        self.assertEqual(1, pruned)


if __name__ == '__main__':
    unittest.main()
