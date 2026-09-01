"""Populate the database with sample students/courses/enrollments.

Run once with: python -m app.seed
Replace the sample rows below with a real import (CSV/엑셀 등) from the
학사 시스템 when wiring this up to real data.
"""
from app.db import get_conn, init_db


STUDENTS = [
    # student_number, name, department, grade, class_no, advisor_professor
    ("20231001", "홍길동", "컴퓨터소프트웨어과", "2", "A", "김철수"),
    ("20231002", "이영희", "컴퓨터소프트웨어과", "2", "A", "김철수"),
    ("20221015", "박민수", "간호학과", "3", "B", "정수진"),
]

COURSES = [
    # course_name, professor, class_day, class_time
    ("자료구조", "김철수", "월", "1~2교시"),
    ("데이터베이스", "이영수", "화", "3~4교시"),
    ("운영체제", "박정민", "수", "1~2교시"),
    ("성인간호학", "정수진", "목", "5~6교시"),
]

# student_number -> list of course_name enrolled in
ENROLLMENTS = {
    "20231001": ["자료구조", "데이터베이스"],
    "20231002": ["자료구조", "운영체제"],
    "20221015": ["성인간호학"],
}


def seed():
    init_db()
    conn = get_conn()
    cur = conn.cursor()

    for number, name, dept, grade, class_no, advisor in STUDENTS:
        cur.execute(
            """INSERT INTO students (student_number, name, department, grade, class_no, advisor_professor)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(student_number) DO UPDATE SET
                 name=excluded.name, department=excluded.department,
                 grade=excluded.grade, class_no=excluded.class_no,
                 advisor_professor=excluded.advisor_professor""",
            (number, name, dept, grade, class_no, advisor),
        )

    for course_name, professor, class_day, class_time in COURSES:
        cur.execute(
            """INSERT INTO courses (course_name, professor, class_day, class_time)
               SELECT ?, ?, ?, ?
               WHERE NOT EXISTS (
                 SELECT 1 FROM courses WHERE course_name=? AND professor=?
               )""",
            (course_name, professor, class_day, class_time, course_name, professor),
        )

    for student_number, course_names in ENROLLMENTS.items():
        student_id = cur.execute(
            "SELECT id FROM students WHERE student_number=?", (student_number,)
        ).fetchone()["id"]
        for course_name in course_names:
            course_id = cur.execute(
                "SELECT id FROM courses WHERE course_name=?", (course_name,)
            ).fetchone()["id"]
            cur.execute(
                "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?, ?)",
                (student_id, course_id),
            )

    conn.commit()
    conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
