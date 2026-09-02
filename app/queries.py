"""Read helpers shared by the Flask routes."""
from datetime import timedelta

from app.config import MAX_ABSENCE_PERIODS_PER_COURSE, TERM, WEEKDAY_ORDER


def format_schedule(slots):
    """Human-readable weekly schedule, e.g. ('화, 목', '3~4교시, 1~2교시') --
    reference text only; actual 결석 교시 selection happens per class_date."""
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
    first."""
    slots = conn.execute(
        "SELECT day, period_start, period_end FROM course_slots WHERE course_id=%s",
        (course_id,),
    ).fetchall()
    return sorted(slots, key=lambda s: (WEEKDAY_ORDER.index(s["day"]), s["period_start"]))


def get_valid_class_dates(conn, course_id, period_start, period_end):
    """Every calendar date in [period_start, period_end] that lands on one
    of this course's weekly meeting days, each paired with that slot's
    period range -- the choices for "수업일" on the review step. Earliest
    date first (so 결석 시작일, or the closest date to it, is the default)."""
    slots = get_course_slots(conn, course_id)
    if not slots:
        return []
    slot_by_day = {}
    for s in slots:
        slot_by_day.setdefault(s["day"], s)  # first slot wins if a day repeats

    dates = []
    day = period_start
    while day <= period_end:
        slot = slot_by_day.get(WEEKDAY_ORDER[day.weekday()])
        if slot is not None:
            dates.append({
                "date": day,
                "period_start": slot["period_start"],
                "period_end": slot["period_end"],
            })
        day += timedelta(days=1)
    return dates


def get_course_used_hours(conn, student_id, course_id, term=TERM):
    """Sum of 교시 hours already recorded for this student+course across
    every 출석인정허가원 issued this term (permit_records), used to cap the
    cumulative total under MAX_ABSENCE_PERIODS_PER_COURSE."""
    row = conn.execute(
        """SELECT COALESCE(SUM(periods_missed), 0) AS total FROM permit_records
           WHERE term=%s AND student_id=%s AND course_id=%s""",
        (term, student_id, course_id),
    ).fetchone()
    return row["total"]


def get_student_courses(conn, student, term=TERM):
    """Courses offered to this student's (grade, section) this term, each
    annotated with cumulative hours already used and hours still available
    before hitting the 12시간 cap."""
    rows = conn.execute(
        """SELECT * FROM courses WHERE term=%s AND grade=%s AND section=%s
           ORDER BY course_name""",
        (term, student["grade"], student["class_no"]),
    ).fetchall()

    courses = []
    for c in rows:
        slots = get_course_slots(conn, c["id"])
        class_day, class_time = format_schedule(slots)
        used_hours = get_course_used_hours(conn, student["id"], c["id"], term)
        remaining = max(0, MAX_ABSENCE_PERIODS_PER_COURSE - used_hours)
        courses.append({
            "id": c["id"],
            "course_name": c["course_name"],
            "professor": c["professor"],
            "class_day": class_day,
            "class_time": class_time,
            "used_hours": used_hours,
            "remaining_hours": remaining,
            "at_limit": remaining <= 0,
        })
    return courses
