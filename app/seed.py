"""Small hand-written dataset for local smoke-testing without the real
학사 데이터 files. For real data use `python -m app.import_real_data`
instead (see README.md).

Run with: python -m app.seed
"""
from app.config import TERM
from app.db import get_conn, init_db

STUDENTS = [
    # student_number, name, department, grade, class_no
    ("20231001", "홍길동", "컴퓨터소프트웨어과", "2", "A"),
    ("20231002", "이영희", "컴퓨터소프트웨어과", "2", "A"),
]

# (grade, section, course_name, professor, [(day, period_start, period_end), ...])
COURSES = [
    ("2", "A", "자료구조", "김철수", [("월", 1, 2)]),
    ("2", "A", "데이터베이스", "이영수", [("화", 3, 4)]),
]


def seed():
    init_db()
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM students WHERE term=?", (TERM,))
    for number, name, dept, grade, class_no in STUDENTS:
        cur.execute(
            """INSERT INTO students (term, student_number, name, department, grade, class_no)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (TERM, number, name, dept, grade, class_no),
        )

    for grade, section, course_name, professor, slots in COURSES:
        cur.execute(
            """INSERT INTO courses (term, grade, section, course_name, professor)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(term, grade, section, course_name, professor) DO NOTHING""",
            (TERM, grade, section, course_name, professor),
        )
        course_id = cur.execute(
            """SELECT id FROM courses WHERE term=? AND grade=? AND section=?
               AND course_name=? AND professor=?""",
            (TERM, grade, section, course_name, professor),
        ).fetchone()["id"]
        for day, start, end in slots:
            cur.execute(
                """INSERT INTO course_slots (course_id, day, period_start, period_end)
                   VALUES (?, ?, ?, ?)""",
                (course_id, day, start, end),
            )

    conn.commit()
    conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
