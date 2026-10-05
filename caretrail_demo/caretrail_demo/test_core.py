"""
Tests for the CareTrail demo core.

Run: python -m unittest test_core
Requires: Python 3.11+ (we're on 3.14).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

from caretrail_demo import core


HERE = Path(__file__).parent
SCHEMA = HERE / "caretrail_demo" / "schema.sql"
MODULE = HERE / "content" / "caretrail_vl_eac_module.json"


class CoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.conn = core.connect(self.tmp.name)
        core.init_db(self.conn, SCHEMA)
        self.ids = core.seed_demo(self.conn, MODULE)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    # --- cascade ---

    def test_cascade_computes_percentages(self):
        module = json.loads(MODULE.read_text())
        values = {1: 200, 2: 160, 3: 128, 4: 32, 5: 8, 6: 4, 7: 3, 8: 12}
        results, weakest = core.compute_cascade(module, values)
        by_step = {r["step"]: r for r in results}
        self.assertEqual(by_step[2]["percent"], 80.0)   # VL coverage
        self.assertEqual(by_step[3]["percent"], 80.0)   # suppression
        self.assertEqual(by_step[5]["percent"], 25.0)   # EAC start
        self.assertEqual(by_step[6]["percent"], 12.5)   # EAC completion
        self.assertIsNotNone(weakest)
        # weakest has the lowest percent: step 7 (9.4%) — wait,
        # step 7 is 3/32 = 9.4, lower than step 6 = 12.5.
        self.assertEqual(weakest["step"], 7)

    def test_cascade_handles_zero_parent(self):
        module = json.loads(MODULE.read_text())
        values = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0}
        results, weakest = core.compute_cascade(module, values)
        for r in results:
            if "percent_of" in next(s for s in module["cascade"]["steps"] if s["step"] == r["step"]):
                self.assertIsNone(r["percent"])
        self.assertIsNone(weakest)

    # --- due date rule ---

    def test_action_requires_due_date(self):
        with self.assertRaises(ValueError):
            core.create_action(
                self.conn,
                finding_id=self.ids["finding"],
                task="no date",
                lead_owner_id="PER-CHANDA",
                due_date="",
                critical=False,
                root_cause_code="systems",
            )

    def test_action_with_due_date_succeeds(self):
        aid = core.create_action(
            self.conn,
            finding_id=self.ids["finding"],
            task="has a date",
            lead_owner_id="PER-CHANDA",
            due_date="2026-10-20",
            critical=False,
            root_cause_code="knowledge",
        )
        self.assertTrue(aid.startswith("ACT-"))

    # --- status flow ---

    def test_new_action_is_open(self):
        status = core.current_status(self.conn, self.ids["action_systems"])
        self.assertEqual(status, "open")

    def test_status_change_is_append_only(self):
        core.change_status(self.conn, self.ids["action_systems"], "in_progress", "PER-CHANDA")
        core.change_status(self.conn, self.ids["action_systems"], "done", "PER-CHANDA")
        core.change_status(self.conn, self.ids["action_systems"], "verified", "PER-BWALYA")
        status = core.current_status(self.conn, self.ids["action_systems"])
        self.assertEqual(status, "verified")
        # And we can see all three events in order.
        rows = self.conn.execute(
            "SELECT status FROM status_event WHERE action_id = ? ORDER BY occurred_at, rowid",
            (self.ids["action_systems"],),
        ).fetchall()
        statuses = [r["status"] for r in rows]
        self.assertEqual(statuses, ["open", "in_progress", "done", "verified"])

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            core.change_status(self.conn, self.ids["action_systems"], "banana", "PER-CHANDA")

    # --- overdue is computed ---

    def test_overdue_pure_function(self):
        self.assertTrue(core.is_overdue("2026-01-01", "open", on_date="2026-10-05"))
        self.assertFalse(core.is_overdue("2026-12-01", "open", on_date="2026-10-05"))
        self.assertFalse(core.is_overdue("2026-01-01", "closed", on_date="2026-10-05"))
        self.assertFalse(core.is_overdue("2026-01-01", "verified", on_date="2026-10-05"))
        self.assertTrue(core.is_overdue("2026-10-04", "blocked", on_date="2026-10-05"))

    # --- district view rule ---

    def test_district_view_shows_resources_and_systems_only(self):
        view = core.district_view(self.conn)
        tasks = [a["task"] for a in view["needs_district_decision"]]
        # systems action (facility lead) and resources action (district) both included
        self.assertTrue(any("VL tracking logbook" in t for t in tasks))
        self.assertTrue(any("DTG stock-out" in t for t in tasks))

    def test_district_view_includes_critical_even_if_not_resources(self):
        # Add a critical knowledge action. It should still appear at district level.
        aid = core.create_action(
            self.conn,
            finding_id=self.ids["finding"],
            task="critical knowledge gap",
            lead_owner_id="PER-CHANDA",
            due_date="2026-10-20",
            critical=True,
            root_cause_code="knowledge",
        )
        view = core.district_view(self.conn)
        ids = [a["action_id"] for a in view["needs_district_decision"]]
        self.assertIn(aid, ids)

    # --- facility view ---

    def test_facility_view_lists_actions(self):
        actions = core.facility_actions(self.conn, self.ids["facility"])
        self.assertEqual(len(actions), 2)
        # Critical first
        self.assertTrue(actions[0]["critical"])


if __name__ == "__main__":
    unittest.main()
