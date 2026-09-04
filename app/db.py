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
    # UNIQUE on the identifying columns makes re-importing the same
    # timetable idempotent (ON CONFLICT DO NOTHING in load_timetable)
    # instead of silently doubling up every slot on every re-upload.
    """
    CREATE TABLE IF NOT EXISTS course_slots (
        id SERIAL PRIMARY KEY,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        period_start INTEGER NOT NULL,
        period_end INTEGER NOT NULL,
        building TEXT,
        room TEXT,
        UNIQUE (course_id, day, period_start, period_end)
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
    # 보강(makeup class) overrides from the 보강결과보고서 upload: on
    # cancelled_date the course's normal weekly slot didn't actually meet
    # (결강), and it met instead on makeup_date/period range (which may
    # fall on a different weekday than the course's usual schedule). Used
    # to correct the "수업일" choices offered in the review step.
    """
    CREATE TABLE IF NOT EXISTS course_makeups (
        id SERIAL PRIMARY KEY,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        cancelled_date DATE NOT NULL,
        makeup_date DATE NOT NULL,
        period_start INTEGER NOT NULL,
        period_end INTEGER NOT NULL,
        reason TEXT,
        UNIQUE (course_id, cancelled_date, makeup_date)
    )
    """,
    # Course names shown to every student in their grade regardless of
    # section (see config.py's old ELECTIVE_COURSE_NAMES comment) --
    # editable from the 관리자 업로드 page instead of hardcoded, so a new
    # term's electives don't need a code change/redeploy.
    """
    CREATE TABLE IF NOT EXISTS elective_courses (
        course_name TEXT PRIMARY KEY
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
    # course_slots had no uniqueness guard before this, so re-uploading the
    # same timetable duplicated every slot (visible as "월, 월" / "6~8교시,
    # 6~8교시" doubled up in the course list). Drop the extras (keep the
    # lowest id per group) before adding the index that prevents new ones.
    """
    DELETE FROM course_slots a USING course_slots b
    WHERE a.id > b.id AND a.course_id = b.course_id AND a.day = b.day
      AND a.period_start = b.period_start AND a.period_end = b.period_end
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS course_slots_unique_idx "
    "ON course_slots (course_id, day, period_start, period_end)",
    # One-time seed for the deployments that already had these two
    # hardcoded in ELECTIVE_COURSE_NAMES before it moved into this table --
    # only fires while the table is still completely empty, so it never
    # re-adds a name an admin has since deleted (this runs on every
    # startup, same as every other migration here).
    """
    INSERT INTO elective_courses (course_name)
    SELECT name FROM (VALUES ('애플리케이션프레임워크'), ('빅데이터프로그래밍')) AS v(name)
    WHERE NOT EXISTS (SELECT 1 FROM elective_courses)
    """,
    # elective_courses started as a flat, ungraded list -- a course_name
    # could only be registered once, period, even though the same name
    # could legitimately be a different elective in different grades. Adds
    # a grade column (backfilling existing rows as '3', since the only
    # electives that existed before this were 3학년-specific) and swaps
    # the old course_name-only uniqueness for a (grade, course_name) one,
    # via an index rather than a new PRIMARY KEY so this stays safely
    # re-runnable on every startup (ADD CONSTRAINT has no IF NOT EXISTS).
    "ALTER TABLE elective_courses ADD COLUMN IF NOT EXISTS grade TEXT",
    "UPDATE elective_courses SET grade = '3' WHERE grade IS NULL",
    "ALTER TABLE elective_courses ALTER COLUMN grade SET NOT NULL",
    "ALTER TABLE elective_courses DROP CONSTRAINT IF EXISTS elective_courses_pkey",
    "CREATE UNIQUE INDEX IF NOT EXISTS elective_courses_unique_idx "
    "ON elective_courses (grade, course_name)",
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
