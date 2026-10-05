"""
CareTrail demo core rules.

Scope: VL/EAC module only. Synthetic data only. Demo only.

These are the rules from the charter and the plan, expressed as pure
functions where possible, so they are easy to test.

Verified by: static reading. NOT verified by execution (see STATUS.md).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

VALID_STATUSES = {
    "open", "in_progress", "done", "blocked",
    "verified", "closed", "rejected",
}

VALID_ROOT_CAUSES = {
    "knowledge", "skills", "systems", "resources", "data", "other",
}

# Root causes that route to the district (visible at district level)
DISTRICT_VISIBLE_ROOT_CAUSES = {"resources", "systems"}

# How many days after 'done' before the verification chain escalates
VERIFY_ESCALATE_TO_FACILITY_SUPERVISOR_DAYS = 7
VERIFY_ESCALATE_TO_DISTRICT_ROLE_DAYS = 14


# ---------------------------------------------------------------------
# IDs and time
# ---------------------------------------------------------------------

def new_id(prefix: str) -> str:
    """Stable, readable ID for the demo. Not a UUID; demo only."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------

def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str | Path) -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


# ---------------------------------------------------------------------
# Cascade logic
# ---------------------------------------------------------------------

def compute_cascade(module: dict[str, Any], values: dict[int, int]) -> list[dict[str, Any]]:
    """
    Given a module dict (from caretrail_vl_eac_module.json) and a dict of
    step_number -> value, return a list of step results with percentages
    and the weakest step identified.

    Weakest step rule (demo): among steps that have a 'percent_of' parent,
    the one with the lowest percentage is weakest. Ties go to the earlier
    step. Steps without a parent are excluded from the 'weakest' choice.
    """
    steps = module["cascade"]["steps"]
    results: list[dict[str, Any]] = []
    weakest: dict[str, Any] | None = None

    for step in steps:
        n = step["step"]
        value = values.get(n, 0)
        percent = None
        if "percent_of" in step:
            parent_value = values.get(step["percent_of"], 0)
            if parent_value > 0:
                percent = round(100.0 * value / parent_value, 1)
            else:
                percent = None  # not enough data

        row = {
            "step": n,
            "question": step["question"],
            "value": value,
            "label": step.get("label"),
            "percent": percent,
        }
        results.append(row)

        if percent is not None:
            if weakest is None or percent < weakest["percent"]:
                weakest = row

    return results, weakest


# ---------------------------------------------------------------------
# Status flow rules
# ---------------------------------------------------------------------

def current_status(conn: sqlite3.Connection, action_id: str) -> str:
    """The status of the most recent status_event for an action."""
    row = conn.execute(
        """
        SELECT status FROM status_event
        WHERE action_id = ?
        ORDER BY occurred_at DESC, rowid DESC
        LIMIT 1
        """,
        (action_id,),
    ).fetchone()
    return row["status"] if row else "open"


def is_overdue(action_due_date: str, status: str, on_date: str | None = None) -> bool:
    """
    Overdue is computed, never stored (decision D4).
    An action is overdue if its due date is before today and it is not
    in a terminal state.
    """
    terminal = {"verified", "closed", "rejected"}
    if status in terminal:
        return False
    on_date = on_date or today_iso()
    return action_due_date < on_date


def days_since(iso_timestamp: str, now: str | None = None) -> int:
    """Whole days between an ISO timestamp and now (default: now)."""
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now_dt = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    return (now_dt - then).days


# ---------------------------------------------------------------------
# Action creation
# ---------------------------------------------------------------------

def create_finding(
    conn: sqlite3.Connection,
    visit_id: str,
    step_number: int,
    step_label: str,
    root_cause_code: str,
    note: str = "",
) -> str:
    if root_cause_code not in VALID_ROOT_CAUSES:
        raise ValueError(f"invalid root cause: {root_cause_code}")
    finding_id = new_id("FND")
    conn.execute(
        """
        INSERT INTO finding
            (finding_id, visit_id, step_number, step_label,
             root_cause_code, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (finding_id, visit_id, step_number, step_label,
         root_cause_code, note, utc_now_iso()),
    )
    conn.commit()
    return finding_id


def create_action(
    conn: sqlite3.Connection,
    finding_id: str,
    task: str,
    lead_owner_id: str,
    due_date: str,
    critical: bool,
    root_cause_code: str,
) -> str:
    if root_cause_code not in VALID_ROOT_CAUSES:
        raise ValueError(f"invalid root cause: {root_cause_code}")
    if not due_date or due_date.strip() == "":
        # Enforced by CHECK too, but we catch it early with a clear message.
        raise ValueError("every action must have a calendar due date")
    action_id = new_id("ACT")
    conn.execute(
        """
        INSERT INTO action
            (action_id, finding_id, task, lead_owner_id, due_date,
             critical, root_cause_code, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (action_id, finding_id, task, lead_owner_id, due_date,
         1 if critical else 0, root_cause_code, utc_now_iso()),
    )
    # Every action starts with an 'open' status event.
    conn.execute(
        """
        INSERT INTO status_event
            (status_event_id, action_id, status, actor_id, note, occurred_at)
        VALUES (?, ?, 'open', ?, '', ?)
        """,
        (new_id("EVT"), action_id, lead_owner_id, utc_now_iso()),
    )
    conn.commit()
    return action_id


def change_status(
    conn: sqlite3.Connection,
    action_id: str,
    new_status: str,
    actor_id: str,
    note: str = "",
) -> None:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status}")
    conn.execute(
        """
        INSERT INTO status_event
            (status_event_id, action_id, status, actor_id, note, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id("EVT"), action_id, new_status, actor_id, note, utc_now_iso()),
    )
    conn.commit()


# ---------------------------------------------------------------------
# Views for the demo
# ---------------------------------------------------------------------

def facility_actions(conn: sqlite3.Connection, facility_id: str) -> list[dict[str, Any]]:
    """All actions at a facility, with computed status and overdue flag."""
    rows = conn.execute(
        """
        SELECT a.action_id, a.task, a.due_date, a.critical, a.root_cause_code,
               p.display_name AS lead_owner_name,
               f.step_label, f.step_number
        FROM action a
        JOIN finding f ON f.finding_id = a.finding_id
        JOIN visit   v ON v.visit_id   = f.visit_id
        JOIN person  p ON p.person_id  = a.lead_owner_id
        WHERE v.facility_id = ?
        ORDER BY a.critical DESC, a.due_date ASC
        """,
        (facility_id,),
    ).fetchall()

    out = []
    for r in rows:
        status = current_status(conn, r["action_id"])
        out.append({
            "action_id": r["action_id"],
            "task": r["task"],
            "lead_owner_name": r["lead_owner_name"],
            "due_date": r["due_date"],
            "critical": bool(r["critical"]),
            "root_cause_code": r["root_cause_code"],
            "step_label": r["step_label"],
            "step_number": r["step_number"],
            "status": status,
            "overdue": is_overdue(r["due_date"], status),
        })
    return out


def district_view(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """
    District-level view.
    Rule: only actions whose root cause is Resources or Systems are shown
    at district level (decision from the plan).
    Also show anything critical, regardless of root cause.
    """
    rows = conn.execute(
        """
        SELECT a.action_id, a.task, a.due_date, a.critical, a.root_cause_code,
               p.display_name AS lead_owner_name,
               fac.name AS facility_name
        FROM action a
        JOIN finding  f   ON f.finding_id = a.finding_id
        JOIN visit    v   ON v.visit_id   = f.visit_id
        JOIN facility fac ON fac.facility_id = v.facility_id
        JOIN person   p   ON p.person_id  = a.lead_owner_id
        ORDER BY a.critical DESC, a.due_date ASC
        """,
    ).fetchall()

    needs_district = []
    for r in rows:
        status = current_status(conn, r["action_id"])
        include = (
            r["root_cause_code"] in DISTRICT_VISIBLE_ROOT_CAUSES
            or bool(r["critical"])
        )
        if not include:
            continue
        needs_district.append({
            "action_id": r["action_id"],
            "task": r["task"],
            "facility_name": r["facility_name"],
            "lead_owner_name": r["lead_owner_name"],
            "due_date": r["due_date"],
            "critical": bool(r["critical"]),
            "root_cause_code": r["root_cause_code"],
            "status": status,
            "overdue": is_overdue(r["due_date"], status),
            "blocked": status == "blocked",
        })

    return {"needs_district_decision": needs_district}


# ---------------------------------------------------------------------
# Seeding for the demo
# ---------------------------------------------------------------------

def seed_demo(conn: sqlite3.Connection, module_path: str | Path) -> dict[str, Any]:
    """
    Seed the demo database with fictional facilities, people, a visit,
    cascade entries, a finding, and two actions.
    Returns a dict of IDs we'll use in the demo script.
    """
    module = json.loads(Path(module_path).read_text(encoding="utf-8"))

    ids: dict[str, Any] = {}

    # Facilities
    ids["facility"] = "FAC-CHIMWEMWE"
    conn.execute(
        "INSERT INTO facility (facility_id, name, district) VALUES (?, ?, ?)",
        (ids["facility"], "Chimwemwe Health Post", "Mpika"),
    )

    # People
    people = [
        ("PER-BWALYA",  "M. Bwalya",   "mentor",         None),
        ("PER-CHANDA",  "S. Chanda",   "facility_lead",  ids["facility"]),
        ("PER-MULENGA", "K. Mulenga",  "district_role",  None),
        ("PER-TEMBO",   "J. Tembo",    "data_officer",   ids["facility"]),
    ]
    for pid, name, role, fac in people:
        conn.execute(
            "INSERT INTO person (person_id, display_name, role, facility_id) VALUES (?, ?, ?, ?)",
            (pid, name, role, fac),
        )

    # A visit
    ids["visit"] = "VIS-001"
    conn.execute(
        """
        INSERT INTO visit (visit_id, facility_id, mentor_id, visit_date, module_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ids["visit"], ids["facility"], "PER-BWALYA", today_iso(), module["module_id"]),
    )

    # Cascade counts for the demo.
    # Chosen so the story is clear: VL coverage is decent (80%),
    # but EAC start is poor (25%), so EAC start is the weakest step.
    cascade_values = {
        1: 200,   # on ART 6+ months
        2: 160,   # have VL result in last 12 months  -> 80% coverage
        3: 128,   # suppressed                        -> 80% suppression
        4: 32,    # unsuppressed
        5: 8,     # started EAC                      -> 25% (weakest)
        6: 4,     # completed >= 4 sessions          -> 12.5%
        7: 3,     # repeat VL done                   -> 9.4%
        8: 12,    # pregnant/breastfeeding with VL
    }
    for step, value in cascade_values.items():
        conn.execute(
            "INSERT INTO cascade_entry (cascade_entry_id, visit_id, step_number, value) VALUES (?, ?, ?, ?)",
            (new_id("CAS"), ids["visit"], step, value),
        )

    # Finding: weakest step is EAC start (step 5).
    ids["finding"] = create_finding(
        conn,
        visit_id=ids["visit"],
        step_number=5,
        step_label="EAC start rate",
        root_cause_code="systems",
        note="No VL tracking logbook; EAC starts are not scheduled at the point of the result.",
    )

    # Action 1: facility-level, systems. Not critical.
    ids["action_systems"] = create_action(
        conn,
        finding_id=ids["finding"],
        task="Set up a weekly VL tracking logbook and assign one person to update it daily.",
        lead_owner_id="PER-CHANDA",
        due_date="2026-10-17",
        critical=False,
        root_cause_code="systems",
    )

    # Action 2: district-level, resources. Critical.
    ids["action_resources"] = create_action(
        conn,
        finding_id=ids["finding"],
        task="Escalate DTG stock-out to District Pharmacist and confirm resupply date.",
        lead_owner_id="PER-MULENGA",
        due_date="2026-10-12",
        critical=True,
        root_cause_code="resources",
    )

    conn.commit()
    return ids
