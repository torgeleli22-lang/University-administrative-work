"""Read helpers shared by the Flask routes.

Neon is a remote DB, so every query is a network round trip -- these are
written to fetch in bulk (one query for N courses) rather than looping a
query per course, which is what made student/review pages slow before.
"""
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


def get_slots_by_course(conn, course_ids):
    """{course_id: [slot, ...]} for every id in course_ids, in one query."""
    course_ids = list(course_ids)
    by_course = {cid: [] for cid in course_ids}
    if not course_ids:
        return by_course
    rows = conn.execute(
        """SELECT course_id, day, period_start, period_end FROM course_slots
           WHERE course_id = ANY(%s)""",
        (course_ids,),
    ).fetchall()
    for r in rows:
        by_course[r["course_id"]].append(r)
    for cid in by_course:
        by_course[cid].sort(key=lambda s: (WEEKDAY_ORDER.index(s["day"]), s["period_start"]))
    return by_course


def valid_class_dates(slots, period_start, period_end):
    """Every calendar date in [period_start, period_end] that lands on one
    of this course's weekly meeting days, each paired with that slot's
    period range -- the choices for "수업일" on the review step. Earliest
    date first (so 결석 시작일, or the closest date to it, is the default)."""
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


def get_used_hours_by_course(conn, student_id, course_ids, term=TERM):
    """{course_id: cumulative 교시 hours already on file} for every id in
    course_ids, in one query."""
    course_ids = list(course_ids)
    used = {cid: 0 for cid in course_ids}
    if not course_ids:
        return used
    rows = conn.execute(
        """SELECT course_id, COALESCE(SUM(periods_missed), 0) AS total FROM permit_records
           WHERE term=%s AND student_id=%s AND course_id = ANY(%s)
           GROUP BY course_id""",
        (term, student_id, course_ids),
    ).fetchall()
    for r in rows:
        used[r["course_id"]] = r["total"]
    return used


def get_student_permit_records(conn, student_id, term=TERM):
    """This student's own 출석인정허가원 history for the term -- shown while
    filling out a new one so the office can see at a glance what's already
    on file (and how close each course is to the 12시간 cap) without
    switching to the 사용 기록 tab."""
    return conn.execute(
        """SELECT p.id, p.created_at, c.course_name, c.professor, p.reason_code,
                  p.class_date, p.class_period_start, p.class_period_end, p.periods_missed
           FROM permit_records p
           JOIN courses c ON c.id = p.course_id
           WHERE p.term = %s AND p.student_id = %s
           ORDER BY p.created_at DESC""",
        (term, student_id),
    ).fetchall()


def get_student_courses(conn, student, term=TERM):
    """Courses offered to this student's (grade, section) this term, each
    annotated with cumulative hours already used and hours still available
    before hitting the 12시간 cap. Three queries total, regardless of how
    many courses the student has."""
    rows = conn.execute(
        """SELECT * FROM courses WHERE term=%s AND grade=%s AND section=%s
           ORDER BY course_name""",
        (term, student["grade"], student["class_no"]),
    ).fetchall()

    course_ids = [c["id"] for c in rows]
    slots_by_course = get_slots_by_course(conn, course_ids)
    used_by_course = get_used_hours_by_course(conn, student["id"], course_ids, term)

    courses = []
    for c in rows:
        class_day, class_time = format_schedule(slots_by_course[c["id"]])
        used_hours = used_by_course[c["id"]]
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
