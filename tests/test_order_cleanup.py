from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from core.database import Database
from tools import clean_logs


class OrderCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.db_path = root / "quantrading.db"
        self.backup_dir = root / "order_cleanup_backups"
        self.config_patch = patch.object(config, "DB_PATH", str(self.db_path))
        self.backup_patch = patch.object(
            clean_logs, "ORDER_BACKUP_DIR", self.backup_dir
        )
        self.config_patch.start()
        self.backup_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.backup_patch.stop)

    def _seed(self) -> tuple[int, int]:
        db = Database()
        db.connect()
        db.cursor.execute(
            """
            INSERT INTO orders
                (symbol, action, order_type, quantity, status, created_at)
            VALUES
                ('OLD', 'BUY', 'MKT', 1, 'Filled', datetime('now', '-40 days'))
            """
        )
        old_id = int(db.cursor.lastrowid)
        db.cursor.execute(
            """
            INSERT INTO orders
                (symbol, action, order_type, quantity, status, created_at)
            VALUES
                ('RECENT', 'BUY', 'MKT', 1, 'Filled', datetime('now', '-2 days'))
            """
        )
        recent_id = int(db.cursor.lastrowid)
        db.conn.close()
        return old_id, recent_id

    def test_database_filters_and_deletes_only_selected_ids(self):
        old_id, recent_id = self._seed()
        db = Database()
        db.connect()

        recent = db.get_orders(symbol="recent", since_days=7)
        self.assertEqual([row[0] for row in recent], [recent_id])
        self.assertEqual([row[0] for row in db.get_old_orders(days=30)], [old_id])

        self.assertEqual(db.delete_orders_by_ids([old_id]), 1)
        remaining = db.get_orders(limit=10)
        self.assertEqual([row[0] for row in remaining], [recent_id])
        db.conn.close()

    def test_dry_run_preserves_orders_and_real_run_backs_up_before_delete(self):
        old_id, recent_id = self._seed()

        self.assertEqual(clean_logs._clean_orders(days=30, dry_run=True), 1)
        self.assertFalse(self.backup_dir.exists())

        db = Database()
        db.connect()
        self.assertEqual(
            {row[0] for row in db.get_orders(limit=10)}, {old_id, recent_id}
        )
        db.conn.close()

        self.assertEqual(clean_logs._clean_orders(days=30, dry_run=False), 1)
        backups = list(self.backup_dir.glob("orders_before_30d_*.json"))
        self.assertEqual(len(backups), 1)
        payload = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["orders"][0]["id"], old_id)
        self.assertEqual(payload["orders"][0]["symbol"], "OLD")

        db = Database()
        db.connect()
        self.assertEqual([row[0] for row in db.get_orders(limit=10)], [recent_id])
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
