import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "attendance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    student_number TEXT NOT NULL,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    grade TEXT NOT NULL,
    class_no TEXT NOT NULL,
    advisor_professor TEXT,
    UNIQUE (term, student_number)
);

-- One row per (term, grade, section, course_name, professor). A course is
-- offered to a whole 분반 (section) at once, so "who is enrolled" is just
-- "students whose grade/class_no match this course's grade/section" --
-- there is no separate enrollment table to maintain by hand.
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    grade TEXT NOT NULL,
    section TEXT NOT NULL,
    course_name TEXT NOT NULL,
    professor TEXT NOT NULL,
    course_code TEXT,
    credits TEXT,
    UNIQUE (term, grade, section, course_name, professor)
);

-- A course can meet more than once a week; each meeting is one slot.
CREATE TABLE IF NOT EXISTS course_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    building TEXT,
    room TEXT
);

-- Audit log: one row per course covered by a generated permit, so we can
-- enforce "최대 사용 횟수" and let staff see who used what.
CREATE TABLE IF NOT EXISTS permit_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    reason_code INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
