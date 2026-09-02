"""Read helpers shared by the Flask routes."""
from datetime import timedelta

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


def get_course_slots(conn, course_id):
    """This course's weekly meeting slots, sorted 월->일, earliest period
    first -- used to show "몇 교시부터 몇 교시인지" on the review step."""
    slots = conn.execute(
        "SELECT day, period_start, period_end FROM course_slots WHERE course_id=%s",
        (course_id,),
    ).fetchall()
    return sorted(slots, key=lambda s: (WEEKDAY_ORDER.index(s["day"]), s["period_start"]))


def describe_slots(slots):
    """['월요일 1~3교시 (3시간)', ...] for display next to the 결석 교시 선택."""
    lines = []
    for s in slots:
        span = s["period_end"] - s["period_start"] + 1
        period_text = (
            f"{s['period_start']}~{s['period_end']}교시"
            if s["period_start"] != s["period_end"] else f"{s['period_start']}교시"
        )
        lines.append(f"{s['day']}요일 {period_text} ({span}시간)")
    return lines


def count_absence_periods(conn, course_id, period_start, period_end):
    """Total 교시(periods) this course meets between period_start and
    period_end (both inclusive calendar dates), summed over every day in
    the range that lands on one of the course's weekly meeting days.
    A course on periods 1~3 counts as 3 for each such day."""
    slots = get_course_slots(conn, course_id)
    if not slots:
        return 0

    periods_by_day = {}
    for s in slots:
        periods_by_day[s["day"]] = periods_by_day.get(s["day"], 0) + (s["period_end"] - s["period_start"] + 1)

    total = 0
    day = period_start
    while day <= period_end:
        total += periods_by_day.get(WEEKDAY_ORDER[day.weekday()], 0)
        day += timedelta(days=1)
    return total


def get_student_courses(conn, student, term=TERM):
    """Courses offered to this student's (grade, section) this term, each
    annotated with how many times it's already been used and whether it's
    hit the per-course limit."""
    rows = conn.execute(
        """SELECT * FROM courses WHERE term=%s AND grade=%s AND section=%s
           ORDER BY course_name""",
        (term, student["grade"], student["class_no"]),
    ).fetchall()

    usage = {
        r["course_id"]: r["n"]
        for r in conn.execute(
            """SELECT course_id, COUNT(*) AS n FROM permit_records
               WHERE term=%s AND student_id=%s GROUP BY course_id""",
            (term, student["id"]),
        ).fetchall()
    }

    courses = []
    for c in rows:
        slots = conn.execute(
            "SELECT * FROM course_slots WHERE course_id=%s", (c["id"],)
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
