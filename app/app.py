from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file

from app.config import MAX_ABSENCE_PERIODS_PER_COURSE, MAX_USES_PER_COURSE, TERM
from app.db import get_conn, init_db
from app.hwpx_filler import MAX_COURSES, REASON_LABELS, PermitRequest, generate_hwpx
from app.queries import count_absence_periods, describe_slots, get_course_slots, get_student_courses

TEMPLATE_HWPX = Path(__file__).resolve().parent / "assets" / "attendance_permit_template.hwpx"

app = Flask(__name__)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    conn = get_conn()
    if q:
        rows = conn.execute(
            """SELECT * FROM students WHERE term=%s AND (student_number LIKE %s OR name LIKE %s)
               ORDER BY grade, class_no, student_number""",
            (TERM, f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM students WHERE term=%s ORDER BY grade, class_no, student_number",
            (TERM,),
        ).fetchall()
    conn.close()
    return render_template("index.html", students=rows, q=q, term=TERM)


@app.route("/student/<int:student_id>")
def student_form(student_id):
    conn = get_conn()
    student = conn.execute(
        "SELECT * FROM students WHERE id=%s AND term=%s", (student_id, TERM)
    ).fetchone()
    if student is None:
        conn.close()
        abort(404)
    courses = get_student_courses(conn, student)
    conn.close()
    return render_template(
        "form.html",
        student=student,
        courses=courses,
        reasons=REASON_LABELS,
        max_courses=MAX_COURSES,
        max_uses=MAX_USES_PER_COURSE,
        max_absence_periods=MAX_ABSENCE_PERIODS_PER_COURSE,
        today=date.today().isoformat(),
    )


def _parse_period(form):
    period_start = datetime.strptime(form["period_start"], "%Y-%m-%d").date()
    period_end = datetime.strptime(form["period_end"], "%Y-%m-%d").date()
    if period_end < period_start:
        abort(400, "종료일은 시작일보다 앞선 날짜로 설정할 수 없습니다.")
    return period_start, period_end


@app.route("/student/<int:student_id>/review", methods=["POST"])
def review(student_id):
    """Second step: for each selected course, show which 교시 it meets and
    let the office pick exactly how many of those periods were missed
    (capped by both the course's actual schedule in this date range and
    the 과목당 최대 결석 시간 rule) before generating anything."""
    conn = get_conn()
    student = conn.execute(
        "SELECT * FROM students WHERE id=%s AND term=%s", (student_id, TERM)
    ).fetchone()
    if student is None:
        conn.close()
        abort(404)

    reason_code = int(request.form["reason_code"])
    period_start, period_end = _parse_period(request.form)
    course_ids = [int(cid) for cid in request.form.getlist("course_ids")]

    if len(course_ids) > MAX_COURSES:
        conn.close()
        abort(400, f"결석 과목은 최대 {MAX_COURSES}개까지 선택할 수 있습니다.")

    available = {c["id"]: c for c in get_student_courses(conn, student)}
    reviewable = []
    blocked = []
    for cid in course_ids:
        course = available.get(cid)
        if course is None:
            conn.close()
            abort(400, "선택한 과목을 찾을 수 없습니다.")
        if course["at_limit"]:
            blocked.append(f"{course['course_name']} — 이미 이번 학기에 "
                            f"{course['used_count']}회 사용해 신청할 수 없습니다.")
            continue
        max_periods = min(count_absence_periods(conn, cid, period_start, period_end),
                           MAX_ABSENCE_PERIODS_PER_COURSE)
        if max_periods == 0:
            blocked.append(f"{course['course_name']} — 선택하신 기간에는 이 수업이 없습니다.")
            continue
        reviewable.append({
            **course,
            "slot_lines": describe_slots(get_course_slots(conn, cid)),
            "max_periods": max_periods,
            "period_options": list(range(1, max_periods + 1)),
        })
    conn.close()

    return render_template(
        "review.html",
        student=student,
        reason_code=reason_code,
        reason_label=REASON_LABELS[reason_code],
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        reviewable=reviewable,
        blocked=blocked,
    )


@app.route("/student/<int:student_id>/generate", methods=["POST"])
def generate(student_id):
    conn = get_conn()
    student = conn.execute(
        "SELECT * FROM students WHERE id=%s AND term=%s", (student_id, TERM)
    ).fetchone()
    if student is None:
        conn.close()
        abort(404)

    reason_code = int(request.form["reason_code"])
    period_start, period_end = _parse_period(request.form)
    course_ids = [int(cid) for cid in request.form.getlist("course_ids")]

    if len(course_ids) > MAX_COURSES:
        conn.close()
        abort(400, f"결석 과목은 최대 {MAX_COURSES}개까지 선택할 수 있습니다.")

    available = {c["id"]: c for c in get_student_courses(conn, student)}
    selected = []
    for cid in course_ids:
        course = available.get(cid)
        if course is None:
            conn.close()
            abort(400, "선택한 과목을 찾을 수 없습니다.")
        if course["at_limit"]:
            conn.close()
            abort(400, f"'{course['course_name']}' 과목은 이번 학기 출석인정허가원을 이미 "
                        f"{course['used_count']}회 사용해 더 이상 신청할 수 없습니다.")
        # Re-derive the cap server-side rather than trusting the review
        # page's hidden <select> options.
        max_periods = min(count_absence_periods(conn, cid, period_start, period_end),
                           MAX_ABSENCE_PERIODS_PER_COURSE)
        try:
            periods_missed = int(request.form.get(f"periods_{cid}", ""))
        except ValueError:
            conn.close()
            abort(400, f"'{course['course_name']}' 과목의 결석 교시 수를 선택해 주세요.")
        if not (1 <= periods_missed <= max_periods):
            conn.close()
            abort(400, f"'{course['course_name']}' 과목의 결석 교시 수는 1~{max_periods}시간 "
                        f"사이여야 합니다.")
        selected.append({**course, "periods_missed": periods_missed})

    req = PermitRequest(
        student_number=student["student_number"],
        name=student["name"],
        department=student["department"],
        grade=student["grade"],
        class_no=student["class_no"],
        reason_code=reason_code,
        period_start=period_start,
        period_end=period_end,
        courses=selected,
    )

    data = generate_hwpx(str(TEMPLATE_HWPX), req)

    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO permit_records
               (term, student_id, course_id, reason_code, period_start, period_end, periods_missed)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(TERM, student_id, c["id"], reason_code, period_start.isoformat(), period_end.isoformat(),
              c["periods_missed"]) for c in selected],
        )
    conn.commit()
    conn.close()

    filename = f"출석인정허가원_{student['name']}_{period_start.isoformat()}.hwpx"
    return send_file(
        BytesIO(data),
        as_attachment=True,
        download_name=filename,
        mimetype="application/haansofthwpx",
    )


@app.route("/records")
def records():
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.created_at, s.student_number, s.name, s.grade, s.class_no,
                  c.course_name, c.professor, p.reason_code, p.period_start, p.period_end,
                  p.periods_missed
           FROM permit_records p
           JOIN students s ON s.id = p.student_id
           JOIN courses c ON c.id = p.course_id
           WHERE p.term = %s
           ORDER BY p.created_at DESC""",
        (TERM,),
    ).fetchall()
    conn.close()
    return render_template("records.html", records=rows, reasons=REASON_LABELS, term=TERM)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
