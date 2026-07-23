"""情报中心与社区热度的纯逻辑、并发保护和失败降级测试。"""
import threading
import time
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual({'apewisdom': 0, 'reddit_posts': 0, 'stocktwits': 0, 'saved': 0}, result)

    def test_social_mentions_database_round_trip_and_prune(self):
        with tempfile.TemporaryDirectory() as td, patch.object(config, 'DB_PATH', str(Path(td) / 'test.db')):
            db = Database()
            db.connect()
            saved = db.add_social_mentions([
                {'symbol': 'NVDA', 'source': 'apewisdom', 'trade_date': date.today().isoformat(),
                 'mentions': 42, 'rank': 3, 'extra': {'sample': True}},
                {'symbol': 'OLD', 'source': 'apewisdom', 'trade_date': '2000-01-01',
                 'mentions': 1},
            ])
            rows = db.get_social_daily('apewisdom', days=14)
            pruned = db.prune_social_mentions(keep_days=90)
            db.close()

        self.assertEqual(2, saved)
        self.assertEqual('NVDA', rows[0][0])
        self.assertEqual(42, rows[0][2])
        self.assertEqual(1, pruned)


if __name__ == '__main__':
    unittest.main()
