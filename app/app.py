import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from app.config import MAX_ABSENCE_PERIODS_PER_COURSE, PERIOD_CHOICES, TERM
from app.db import get_conn, init_db
from app.hwpx_filler import MAX_COURSES, REASON_LABELS, PermitRequest, generate_hwpx
from app.import_real_data import load_roster, load_timetable, parse_roster, parse_timetable
from app.queries import get_course_used_hours, get_student_courses, get_valid_class_dates
from app.seed import seed as seed_dummy_data

TEMPLATE_HWPX = Path(__file__).resolve().parent / "assets" / "attendance_permit_template.hwpx"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # xls uploads are small; 20MB is a generous cap

# Runs on every import, not just `python app.py` -- gunicorn (used in
# production, see Procfile) imports this module and never executes the
# __main__ block below, so this is the only place guaranteed to run before
# the first request. CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
# are cheap and safe to repeat, so this also self-heals a DB that's behind
# on schema changes (see app/db.py's MIGRATIONS).
init_db()


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    students = []
    if q:
        conn = get_conn()
        students = conn.execute(
            """SELECT * FROM students WHERE term=%s AND (student_number LIKE %s OR name LIKE %s)
               ORDER BY grade, class_no, student_number""",
            (TERM, f"%{q}%", f"%{q}%"),
        ).fetchall()
        conn.close()
    return render_template("index.html", students=students, q=q, term=TERM)


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
        max_absence_periods=MAX_ABSENCE_PERIODS_PER_COURSE,
        today=date.today().isoformat(),
    )


def _parse_period(form):
    start_raw = form.get("period_start", "").strip()
    end_raw = form.get("period_end", "").strip()
    period_start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else date.today()
    period_end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else date.today()
    if period_end < period_start:
        abort(400, "종료일은 시작일보다 앞선 날짜로 설정할 수 없습니다.")
    return period_start, period_end


@app.route("/student/<int:student_id>/review", methods=["POST"])
def review(student_id):
    """Second step: for each selected course, offer a 수업일(date) picker
    limited to dates in the chosen 결석 기간 that the course actually meets
    on (default: the earliest such date, i.e. closest to 결석 시작일), plus
    editable 시작/끝 교시 select boxes defaulting to that date's normal
    slot. Cumulative hours already on file for the course are shown so the
    12시간 cap is visible before submitting."""
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
            blocked.append(f"{course['course_name']} — 이번 학기 누적 {course['used_hours']}시간으로 "
                            f"이미 한도({MAX_ABSENCE_PERIODS_PER_COURSE}시간)에 도달해 신청할 수 없습니다.")
            continue
        valid_dates = get_valid_class_dates(conn, cid, period_start, period_end)
        if not valid_dates:
            blocked.append(f"{course['course_name']} — 선택하신 기간에는 이 수업이 없습니다.")
            continue
        reviewable.append({
            **course,
            "valid_dates": [
                {"iso": d["date"].isoformat(),
                 "label": f"{d['date'].month}/{d['date'].day} ({'월화수목금토일'[d['date'].weekday()]})",
                 "period_start": d["period_start"], "period_end": d["period_end"]}
                for d in valid_dates
            ],
            "period_choices": PERIOD_CHOICES,
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
        max_absence_periods=MAX_ABSENCE_PERIODS_PER_COURSE,
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

        class_date_raw = request.form.get(f"class_date_{cid}", "")
        try:
            class_date = datetime.strptime(class_date_raw, "%Y-%m-%d").date()
        except ValueError:
            conn.close()
            abort(400, f"'{course['course_name']}' 과목의 수업일을 선택해 주세요.")

        valid_dates = {d["date"] for d in get_valid_class_dates(conn, cid, period_start, period_end)}
        if class_date not in valid_dates:
            conn.close()
            abort(400, f"'{course['course_name']}' 과목은 {class_date.month}/{class_date.day}에 "
                        f"수업이 없습니다.")

        try:
            class_period_start = int(request.form.get(f"class_period_start_{cid}", ""))
            class_period_end = int(request.form.get(f"class_period_end_{cid}", ""))
        except ValueError:
            conn.close()
            abort(400, f"'{course['course_name']}' 과목의 수업 시간(교시)을 선택해 주세요.")
        if not (1 <= class_period_start <= class_period_end <= max(PERIOD_CHOICES)):
            conn.close()
            abort(400, f"'{course['course_name']}' 과목의 시작 교시는 끝 교시보다 늦을 수 없습니다.")

        periods_missed = class_period_end - class_period_start + 1
        used_hours = get_course_used_hours(conn, student_id, cid, TERM)
        if used_hours + periods_missed > MAX_ABSENCE_PERIODS_PER_COURSE:
            conn.close()
            remaining = max(0, MAX_ABSENCE_PERIODS_PER_COURSE - used_hours)
            abort(400, f"'{course['course_name']}' 과목은 이번 학기 누적 {used_hours}시간이라 "
                        f"{remaining}시간까지만 추가할 수 있습니다 (신청: {periods_missed}시간).")

        selected.append({
            **course,
            "class_day": f"{class_date.month}/{class_date.day}",
            "class_time": (f"{class_period_start}~{class_period_end}교시"
                            if class_period_start != class_period_end else f"{class_period_start}교시"),
            "class_date_iso": class_date.isoformat(),
            "class_period_start": class_period_start,
            "class_period_end": class_period_end,
            "periods_missed": periods_missed,
        })

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
               (term, student_id, course_id, reason_code, period_start, period_end,
                class_date, class_period_start, class_period_end, periods_missed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(TERM, student_id, c["id"], reason_code, period_start.isoformat(), period_end.isoformat(),
              c["class_date_iso"], c["class_period_start"], c["class_period_end"], c["periods_missed"])
             for c in selected],
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
        """SELECT p.id, p.created_at, s.student_number, s.name, s.grade, s.class_no,
                  c.course_name, c.professor, p.reason_code,
                  p.class_date, p.class_period_start, p.class_period_end, p.periods_missed
           FROM permit_records p
           JOIN students s ON s.id = p.student_id
           JOIN courses c ON c.id = p.course_id
           WHERE p.term = %s
           ORDER BY p.created_at DESC""",
        (TERM,),
    ).fetchall()
    conn.close()
    return render_template("records.html", records=rows, reasons=REASON_LABELS, term=TERM)


def _check_admin_token():
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        abort(500, "서버에 ADMIN_TOKEN 환경변수가 설정되어 있지 않습니다. "
                    "Render 서비스의 Environment 설정에 ADMIN_TOKEN을 추가해 주세요.")
    if request.form.get("admin_token") != expected:
        abort(403, "관리자 토큰이 올바르지 않습니다.")


@app.route("/records/delete", methods=["POST"])
def records_delete():
    _check_admin_token()
    record_id = request.form.get("delete_id")
    conn = get_conn()
    conn.execute("DELETE FROM permit_records WHERE id=%s", (record_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("records"))


@app.route("/admin/import", methods=["GET"])
def admin_import_form():
    return render_template("admin_import.html", term=TERM, result=None)


@app.route("/admin/import", methods=["POST"])
def admin_import():
    """Web version of `python -m app.import_real_data`: upload the school's
    .xls exports straight from the browser and load them into Neon, no
    local Python needed. Files are read into memory and never written to
    disk."""
    _check_admin_token()
    term = request.form.get("term") or TERM

    conn = get_conn()
    log = []
    try:
        roster_file = request.files.get("roster")
        if roster_file and roster_file.filename:
            students = parse_roster(roster_file.read())
            load_roster(conn, students, term)
            log.append(f"학생 {len(students)}명 적재 완료")

        total_slots = 0
        for grade in ["1", "2", "3", "4"]:
            f = request.files.get(f"timetable_{grade}")
            if f and f.filename:
                slots = parse_timetable(f.read(), grade)
                load_timetable(conn, slots, term)
                total_slots += len(slots)
                log.append(f"{grade}학년 시간표: 수업 {len(slots)}건 적재 완료")
        if total_slots:
            log.append(f"총 수업 슬롯 {total_slots}건")

        if not log:
            log.append("업로드된 파일이 없습니다. 최소 하나는 선택해 주세요.")
        else:
            conn.commit()
    except Exception as e:
        conn.rollback()
        log = [f"오류 발생: {e}"]
    finally:
        conn.close()

    return render_template("admin_import.html", term=term, result=log)


@app.route("/admin/seed", methods=["POST"])
def admin_seed():
    """No-file version of /admin/import: loads the small built-in demo
    dataset (app/seed.py) so the whole GitHub->Neon->Render chain can be
    verified from the browser alone, before any real .xls is involved."""
    _check_admin_token()
    seed_dummy_data()
    return render_template(
        "admin_import.html", term=TERM,
        result=["데모(더미) 데이터 적재 완료 — 학생 2명, 과목 2개"],
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
