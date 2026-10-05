-- CareTrail demo schema (SQLite)
-- Scope: VL/EAC module only. Synthetic data only. Demo only.
-- Not the full CareTrail schema. The real schema is caretrail_schema_v1.sql
-- and will be reviewed separately.

PRAGMA foreign_keys = ON;

-- Facilities (fictional, seeded for the demo)
CREATE TABLE facility (
    facility_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    district        TEXT NOT NULL
);

-- People who can own actions (mentor, facility lead, district role)
CREATE TABLE person (
    person_id       TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL,   -- 'mentor' | 'facility_lead' | 'district_role' | 'data_officer'
    facility_id     TEXT,            -- null for district-level people
    FOREIGN KEY (facility_id) REFERENCES facility(facility_id)
);

-- A visit: a mentor, a facility, a date
CREATE TABLE visit (
    visit_id        TEXT PRIMARY KEY,
    facility_id     TEXT NOT NULL,
    mentor_id       TEXT NOT NULL,
    visit_date      TEXT NOT NULL,   -- ISO 8601 date
    module_id       TEXT NOT NULL,   -- 'vl_eac' for the demo
    FOREIGN KEY (facility_id) REFERENCES facility(facility_id),
    FOREIGN KEY (mentor_id)   REFERENCES person(person_id)
);

-- The cascade counts entered during a visit
CREATE TABLE cascade_entry (
    cascade_entry_id TEXT PRIMARY KEY,
    visit_id         TEXT NOT NULL,
    step_number      INTEGER NOT NULL,   -- 1..N
    value            INTEGER NOT NULL,
    FOREIGN KEY (visit_id) REFERENCES visit(visit_id)
);

-- A finding: the weakest step, with a root cause
CREATE TABLE finding (
    finding_id      TEXT PRIMARY KEY,
    visit_id        TEXT NOT NULL,
    step_number     INTEGER NOT NULL,
    step_label      TEXT NOT NULL,
    root_cause_code TEXT NOT NULL,   -- 'knowledge' | 'skills' | 'systems' | 'resources' | 'data' | 'other'
    note            TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (visit_id) REFERENCES visit(visit_id)
);

-- An action: what will be done, by whom, by when
CREATE TABLE action (
    action_id        TEXT PRIMARY KEY,
    finding_id       TEXT NOT NULL,
    task             TEXT NOT NULL,
    lead_owner_id    TEXT NOT NULL,
    due_date         TEXT NOT NULL,   -- ISO 8601 date. NOT NULL: no action without a date.
    critical         INTEGER NOT NULL DEFAULT 0,  -- 0 or 1
    root_cause_code  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (finding_id)    REFERENCES finding(finding_id),
    FOREIGN KEY (lead_owner_id) REFERENCES person(person_id),
    CHECK (due_date <> ''),
    CHECK (critical IN (0, 1))
);

-- Status events: append-only. The current status is derived.
CREATE TABLE status_event (
    status_event_id TEXT PRIMARY KEY,
    action_id       TEXT NOT NULL,
    status          TEXT NOT NULL,   -- 'open' | 'in_progress' | 'done' | 'blocked' | 'verified' | 'closed' | 'rejected'
    actor_id        TEXT NOT NULL,
    note            TEXT,
    occurred_at     TEXT NOT NULL,   -- ISO 8601 datetime, UTC
    FOREIGN KEY (action_id) REFERENCES action(action_id),
    FOREIGN KEY (actor_id)  REFERENCES person(person_id)
);

-- Indexes for the queries we'll actually run
CREATE INDEX idx_status_event_action ON status_event(action_id, occurred_at);
CREATE INDEX idx_action_due          ON action(due_date);
CREATE INDEX idx_action_owner        ON action(lead_owner_id);
