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


def get_makeups_by_course(conn, course_ids):
    """{course_id: [makeup, ...]} for every id in course_ids, in one query
    -- 결강/보강 overrides from the 보강결과보고서 upload."""
    course_ids = list(course_ids)
    by_course = {cid: [] for cid in course_ids}
    if not course_ids:
        return by_course
    rows = conn.execute(
        """SELECT course_id, cancelled_date, makeup_date, period_start, period_end
           FROM course_makeups WHERE course_id = ANY(%s)""",
        (course_ids,),
    ).fetchall()
    for r in rows:
        by_course[r["course_id"]].append(r)
    return by_course


def valid_class_dates(slots, period_start, period_end, makeups=None):
    """Every calendar date in [period_start, period_end] that lands on one
    of this course's weekly meeting days, each paired with that slot's
    period range -- the choices for "수업일" on the review step. 보강
    (makeup) overrides are applied on top: a date the course was 결강
    (cancelled) on is dropped even if it's a normal meeting day, and each
    보강일 (makeup date) is added even if it falls on a day the course
    doesn't normally meet. Earliest date first (so 결석 시작일, or the
    closest date to it, is the default)."""
    makeups = makeups or []
    cancelled_dates = {m["cancelled_date"] for m in makeups}

    slot_by_day = {}
    for s in slots or []:
        slot_by_day.setdefault(s["day"], s)  # first slot wins if a day repeats

    dates = []
    if slots:
        day = period_start
        while day <= period_end:
            slot = slot_by_day.get(WEEKDAY_ORDER[day.weekday()])
            if slot is not None and day not in cancelled_dates:
                dates.append({
                    "date": day,
                    "period_start": slot["period_start"],
                    "period_end": slot["period_end"],
                    "is_makeup": False,
                })
            day += timedelta(days=1)

    for m in makeups:
        if period_start <= m["makeup_date"] <= period_end:
            dates.append({
                "date": m["makeup_date"],
                "period_start": m["period_start"],
                "period_end": m["period_end"],
                "is_makeup": True,
                "replaces": m["cancelled_date"],  # the 결강 date this makeup stands in for
            })

    dates.sort(key=lambda d: d["date"])
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


def get_elective_course_names(conn):
    """Course names shown to every student in their grade regardless of
    section -- see db.py's elective_courses table and config.py's old
    ELECTIVE_COURSE_NAMES comment for why. Managed from the 관리자 업로드
    page (admin_elective_add/delete in app.py)."""
    rows = conn.execute("SELECT course_name FROM elective_courses ORDER BY course_name").fetchall()
    return [r["course_name"] for r in rows]


def add_elective_course(conn, course_name):
    conn.execute(
        "INSERT INTO elective_courses (course_name) VALUES (%s) ON CONFLICT (course_name) DO NOTHING",
        (course_name,),
    )


def delete_elective_course(conn, course_name):
    conn.execute("DELETE FROM elective_courses WHERE course_name = %s", (course_name,))


def get_student_courses(conn, student, term=TERM):
    """Courses offered to this student's (grade, section) this term, plus
    every elective_courses offering in their grade regardless of section
    (see get_elective_course_names -- those 분반 letters are elective
    groups, not the student's homeroom), each annotated with cumulative
    hours already used, hours still available before hitting the 12시간
    cap, and the individual weekdays it meets on (for grouping in the
    결석 과목 selection UI). Four queries total, regardless of how many
    courses the student has."""
    elective_names = get_elective_course_names(conn)
    rows = conn.execute(
        """SELECT * FROM courses WHERE term=%s AND grade=%s
           AND (section=%s OR course_name = ANY(%s))
           ORDER BY course_name""",
        (term, student["grade"], student["class_no"], elective_names),
    ).fetchall()

    course_ids = [c["id"] for c in rows]
    slots_by_course = get_slots_by_course(conn, course_ids)
    used_by_course = get_used_hours_by_course(conn, student["id"], course_ids, term)

    courses = []
    for c in rows:
        slots = slots_by_course[c["id"]]  # already sorted by WEEKDAY_ORDER
        class_day, class_time = format_schedule(slots)
        used_hours = used_by_course[c["id"]]
        remaining = max(0, MAX_ABSENCE_PERIODS_PER_COURSE - used_hours)
        days = []
        for s in slots:
            if s["day"] not in days:
                days.append(s["day"])
        courses.append({
            "id": c["id"],
            "course_name": c["course_name"],
            "professor": c["professor"],
            "class_day": class_day,
            "class_time": class_time,
            "days": days,
            "used_hours": used_hours,
            "remaining_hours": remaining,
            "at_limit": remaining <= 0,
        })
    return courses


def group_courses_by_weekday(courses):
    """Groups courses (each with a 'days' list from get_student_courses)
    by their first weekly meeting day, for the 결석 과목 selection UI --
    lets the office jump straight to "which courses meet on the day the
    student was absent" instead of scanning one flat list. A course
    meeting on more than one day is filed under the earliest of them (its
    other days are still visible in its own class_day text); a course
    with no scheduled slot at all lands in a trailing "요일 미정" group.
    Returns a list of (label, courses) tuples, only for groups that have
    at least one course."""
    by_day = {d: [] for d in WEEKDAY_ORDER}
    unscheduled = []
    for c in courses:
        if not c["days"]:
            unscheduled.append(c)
        else:
            by_day[c["days"][0]].append(c)

    groups = [(f"{d}요일", by_day[d]) for d in WEEKDAY_ORDER if by_day[d]]
    if unscheduled:
        groups.append(("요일 미정", unscheduled))
    return groups
