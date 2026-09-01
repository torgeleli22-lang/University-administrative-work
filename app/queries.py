"""Read helpers shared by the Flask routes."""
from app.config import MAX_USES_PER_COURSE, TERM, WEEKDAY_ORDER


def format_schedule(slots):
    if not slots:
        return "미정", "미정"
    slots = sorted(slots, key=lambda s: (WEEKDAY_ORDER.index(s["day"]), s["period_start"]))
    days = ", ".join(s["day"] for s in slots)
    times = ", ".join(
        f"{s['period_start']}~{s['period_end']}교시" if s["period_start"] != s["period_end"]
        else f"{s['period_start']}교시"
        for s in slots
    )
    return days, times


def get_student_courses(conn, student, term=TERM):
    """Courses offered to this student's (grade, section) this term, each
    annotated with how many times it's already been used and whether it's
    hit the per-course limit."""
    rows = conn.execute(
        """SELECT * FROM courses WHERE term=? AND grade=? AND section=?
           ORDER BY course_name""",
        (term, student["grade"], student["class_no"]),
    ).fetchall()

    usage = dict(conn.execute(
        """SELECT course_id, COUNT(*) AS n FROM permit_records
           WHERE term=? AND student_id=? GROUP BY course_id""",
        (term, student["id"]),
    ).fetchall())

    courses = []
    for c in rows:
        slots = conn.execute(
            "SELECT * FROM course_slots WHERE course_id=?", (c["id"],)
        ).fetchall()
        class_day, class_time = format_schedule(slots)
        used = usage.get(c["id"], 0)
        courses.append({
            "id": c["id"],
            "course_name": c["course_name"],
            "professor": c["professor"],
            "class_day": class_day,
            "class_time": class_time,
            "used_count": used,
            "at_limit": used >= MAX_USES_PER_COURSE,
        })
    return courses
