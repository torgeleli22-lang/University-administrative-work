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
        course_code TEXT,
        credits TEXT,
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
    # Audit log: one row per course covered by a generated permit, so we
    # can enforce "최대 사용 횟수" and let staff see who used what.
    """
    CREATE TABLE IF NOT EXISTS permit_records (
        id SERIAL PRIMARY KEY,
        term TEXT NOT NULL,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        reason_code INTEGER NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def get_conn():
    database_url = os.environ["DATABASE_URL"]
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        for statement in SCHEMA:
            cur.execute(statement)
    conn.commit()
    conn.close()
