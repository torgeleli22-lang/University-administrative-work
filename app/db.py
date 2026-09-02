import os

import psycopg
from psycopg.rows import dict_row

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY,
        term TEXT NOT NULL,
        student_number TEXT NOT NULL,
        name TEXT NOT NULL,
        department TEXT NOT NULL,
        grade TEXT NOT NULL,
        class_no TEXT NOT NULL,
        advisor_professor TEXT,
        UNIQUE (term, student_number)
    )
    """,
    # One row per (term, grade, section, course_name, professor). A course
    # is offered to a whole 분반 (section) at once, so "who is enrolled" is
    # just "students whose grade/class_no match this course's grade/section"
    # -- there is no separate enrollment table to maintain by hand.
    """
    CREATE TABLE IF NOT EXISTS courses (
        id SERIAL PRIMARY KEY,
        term TEXT NOT NULL,
        grade TEXT NOT NULL,
        section TEXT NOT NULL,
        course_name TEXT NOT NULL,
        professor TEXT NOT NULL,
        UNIQUE (term, grade, section, course_name, professor)
    )
    """,
    # A course can meet more than once a week; each meeting is one slot.
    """
    CREATE TABLE IF NOT EXISTS course_slots (
        id SERIAL PRIMARY KEY,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        period_start INTEGER NOT NULL,
        period_end INTEGER NOT NULL,
        building TEXT,
        room TEXT
    )
    """,
    # Audit log: one row per course covered by a generated permit, recording
    # exactly which date and 교시 range was used (so cumulative-hour checks
    # and staff review can both work off real history instead of recomputing
    # from the course's weekly schedule).
    """
    CREATE TABLE IF NOT EXISTS permit_records (
        id SERIAL PRIMARY KEY,
        term TEXT NOT NULL,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        reason_code INTEGER NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        class_date TEXT NOT NULL,
        class_period_start INTEGER NOT NULL,
        class_period_end INTEGER NOT NULL,
        periods_missed INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

# CREATE TABLE IF NOT EXISTS only helps on a brand-new database -- it's a
# no-op against a table that already exists from an earlier version of
# SCHEMA, so columns added or dropped later (like these) never actually
# apply there. ADD/DROP COLUMN IF NOT EXISTS is safe to rerun and heals
# that case too; this runs on every app startup (see app/app.py).
MIGRATIONS = [
    "ALTER TABLE permit_records ADD COLUMN IF NOT EXISTS periods_missed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE permit_records ADD COLUMN IF NOT EXISTS class_date TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE permit_records ADD COLUMN IF NOT EXISTS class_period_start INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE permit_records ADD COLUMN IF NOT EXISTS class_period_end INTEGER NOT NULL DEFAULT 0",
    # 강의계획서 is no longer used as a data source.
    "ALTER TABLE courses DROP COLUMN IF EXISTS course_code",
    "ALTER TABLE courses DROP COLUMN IF EXISTS credits",
]


def get_conn():
    database_url = os.environ["DATABASE_URL"]
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        for statement in SCHEMA:
            cur.execute(statement)
        for statement in MIGRATIONS:
            cur.execute(statement)
    conn.commit()
    conn.close()
