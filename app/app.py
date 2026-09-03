import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from app.config import MAX_ABSENCE_PERIODS_PER_COURSE, PERIOD_CHOICES, TERM
from app.db import get_conn, init_db
from app.hwpx_filler import MAX_COURSES, REASON_LABELS, PermitRequest, generate_hwpx
from app.import_real_data import (
    load_course_makeups,
    load_roster,
    load_timetable,
    parse_makeup_report,
    parse_roster,
    parse_timetable,
)
from app.queries import (
    get_makeups_by_course,
    get_slots_by_course,
    get_student_courses,
    get_student_permit_records,
    get_used_hours_by_course,
    valid_class_dates,
)
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


def render_maybe_partial(partial_template, full_template, **ctx):
    """The single-page apply flow's own fetch() calls set X-Partial so they
    get back just the fragment to inject; a plain browser navigation (JS
    off, direct link, page refresh) has no such header and gets the full
    page instead."""
    template = partial_template if request.headers.get("X-Partial") == "1" else full_template
    return render_template(template, **ctx)


def _search_students(q):
    if not q:
        return []
    conn = get_conn()
    students = conn.execute(
        """SELECT * FROM students WHERE term=%s AND (student_number LIKE %s OR name LIKE %s)
           ORDER BY grade, class_no, student_number""",
        (TERM, f"%{q}%", f"%{q}%"),
    ).fetchall()
    conn.close()
    return students


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    return render_template("index.html", students=_search_students(q), q=q, term=TERM)


@app.route("/students/search")
def students_search():
    """Partial-page endpoint the search box fetches into, so typing doesn't
    reload the whole page -- just the #results div."""
    q = request.args.get("q", "").strip()
    return render_template("_student_results.html", students=_search_students(q), q=q)


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
    history = get_student_permit_records(conn, student_id)
    conn.close()
    return render_maybe_partial(
        "_student_panel.html", "form.html",
        student=student,
        courses=courses,
        history=history,
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
    slots_by_course = get_slots_by_course(conn, course_ids)
    makeups_by_course = get_makeups_by_course(conn, course_ids)
    conn.close()

    reviewable = []
    blocked = []
    for cid in course_ids:
        course = available.get(cid)
        if course is None:
            abort(400, "선택한 과목을 찾을 수 없습니다.")
        if course["at_limit"]:
            blocked.append(f"{course['course_name']} — 이번 학기 누적 {course['used_hours']}시간으로 "
                            f"이미 한도({MAX_ABSENCE_PERIODS_PER_COURSE}시간)에 도달해 신청할 수 없습니다.")
            continue
        valid_dates = valid_class_dates(slots_by_course[cid], period_start, period_end, makeups_by_course[cid])
        if not valid_dates:
            blocked.append(f"{course['course_name']} — 선택하신 기간에는 이 수업이 없습니다.")
            continue
        reviewable.append({
            **course,
            # Rendered as individual clickable date "chips" (not a plain
            # dropdown), so the review template gets the date's parts
            # separately instead of one pre-formatted label string.
            "valid_dates": [
                {"iso": d["date"].isoformat(),
                 "month": d["date"].month, "day": d["date"].day,
                 "weekday": "월화수목금토일"[d["date"].weekday()],
                 "is_makeup": d["is_makeup"],
                 # For a 보강 date, the office needs to see which regular
                 # session it's standing in for right on the chip, not just
                 # that it's "somehow special" -- e.g. "9/18 → 9/22 결강".
                 "replaces_label": (f"{d['replaces'].month}/{d['replaces'].day}"
                                     f"({'월화수목금토일'[d['replaces'].weekday()]})"
                                     if d.get("replaces") else None),
                 "period_start": d["period_start"], "period_end": d["period_end"]}
                for d in valid_dates
            ],
            "period_choices": PERIOD_CHOICES,
        })

    return render_maybe_partial(
        "_review_panel.html", "review.html",
        student=student,
        reason_code=reason_code,
        reason_label=REASON_LABELS[reason_code],
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        reviewable=reviewable,
        blocked=blocked,
        max_absence_periods=MAX_ABSENCE_PERIODS_PER_COURSE,
    )


def _load_selection_context(conn, student, student_id, course_ids):
    """Every piece of DB data needed to validate a course/date/교시
    selection, fetched in the same four bulk queries regardless of how
    many courses were selected. Shared by /confirm and /generate so both
    validate against the exact same data."""
    available = {c["id"]: c for c in get_student_courses(conn, student)}
    slots_by_course = get_slots_by_course(conn, course_ids)
    makeups_by_course = get_makeups_by_course(conn, course_ids)
    used_by_course = get_used_hours_by_course(conn, student_id, course_ids, TERM)
    return available, slots_by_course, makeups_by_course, used_by_course


def _validate_selection(form, course_ids, period_start, period_end,
                         available, slots_by_course, makeups_by_course, used_by_course):
    """Pure validation (no DB access) shared by /confirm and /generate. A
    course can have more than one 수업일 checked at once (class_dates_{id},
    a multi-value checkbox group), each with its own 교시 range
    (period_start_{id}_{iso}/period_end_{id}_{iso}) -- so this returns one
    dict per (course, date) instance, not per course, and the physical
    form ends up with one row per instance (hwpx_filler just zips however
    many dicts come back against its blank rows). A course with none of
    its dates checked is silently dropped rather than erroring, since
    that's how the review step lets someone back out of a course without
    returning to the previous step.

    The 12시간 cap is checked cumulatively as we go through a course's own
    picks in this same submission too (not just against history already on
    file), so e.g. two 6시간 picks for one course in one submission
    correctly hit the cap against each other. Aborts with 400 on the first
    problem found. Each returned dict adds class_day/class_time/
    class_date_iso/class_period_start/class_period_end/periods_missed on
    top of the course's own fields (course_name, professor, ...); used_hours
    is overridden to the running cumulative total *before* this instance,
    for display (used_hours + periods_missed = cumulative after it)."""
    selected = []
    running_used = dict(used_by_course)

    for cid in course_ids:
        course = available.get(cid)
        if course is None:
            abort(400, "선택한 과목을 찾을 수 없습니다.")

        class_dates_raw = form.getlist(f"class_dates_{cid}")
        if not class_dates_raw:
            continue

        valid_dates = {d["date"]: d for d in valid_class_dates(
            slots_by_course[cid], period_start, period_end, makeups_by_course[cid])}

        for class_date_raw in class_dates_raw:
            try:
                class_date = datetime.strptime(class_date_raw, "%Y-%m-%d").date()
            except ValueError:
                abort(400, f"'{course['course_name']}' 과목의 수업일이 올바르지 않습니다.")

            date_info = valid_dates.get(class_date)
            if date_info is None:
                abort(400, f"'{course['course_name']}' 과목은 {class_date.month}/{class_date.day}에 "
                            f"수업이 없습니다.")

            try:
                class_period_start = int(form.get(f"period_start_{cid}_{class_date_raw}", ""))
                class_period_end = int(form.get(f"period_end_{cid}_{class_date_raw}", ""))
            except ValueError:
                abort(400, f"'{course['course_name']}' {class_date.month}/{class_date.day}의 "
                            f"수업 시간(교시)을 선택해 주세요.")
            if not (1 <= class_period_start <= class_period_end <= max(PERIOD_CHOICES)):
                abort(400, f"'{course['course_name']}' {class_date.month}/{class_date.day}의 "
                            f"시작 교시는 끝 교시보다 늦을 수 없습니다.")

            periods_missed = class_period_end - class_period_start + 1
            used_hours = running_used.get(cid, 0)
            if used_hours + periods_missed > MAX_ABSENCE_PERIODS_PER_COURSE:
                remaining = max(0, MAX_ABSENCE_PERIODS_PER_COURSE - used_hours)
                abort(400, f"'{course['course_name']}' 과목은 {class_date.month}/{class_date.day} 포함 "
                            f"이번 학기 누적 {used_hours}시간이라 {remaining}시간까지만 추가할 수 있습니다 "
                            f"(신청: {periods_missed}시간).")
            running_used[cid] = used_hours + periods_missed

            replaces = date_info.get("replaces")
            selected.append({
                **course,
                "used_hours": used_hours,
                "class_day": f"{class_date.month}/{class_date.day}",
                "class_time": (f"{class_period_start}~{class_period_end}교시"
                                if class_period_start != class_period_end else f"{class_period_start}교시"),
                "class_date_iso": class_date.isoformat(),
                "class_period_start": class_period_start,
                "class_period_end": class_period_end,
                "periods_missed": periods_missed,
                "is_makeup": date_info["is_makeup"],
                "replaces_label": f"{replaces.month}/{replaces.day}" if replaces else None,
            })

    if len(selected) > MAX_COURSES:
        abort(400, f"한 번에 신청 가능한 결석 항목(과목 × 수업일)은 최대 {MAX_COURSES}개입니다 "
                    f"(현재 {len(selected)}개). 나눠서 신청해 주세요.")

    return selected


@app.route("/student/<int:student_id>/confirm", methods=["POST"])
def confirm(student_id):
    """Third step: a read-only summary of exactly what will be submitted
    (per course: which date, which 교시, and the resulting cumulative
    hours) with a "확인 및 등록" button that's the only thing that actually
    posts to /generate -- lets the office double check the selection
    before the permit is written to the DB and downloaded, instead of the
    review step's own submit doing that directly."""
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

    context = _load_selection_context(conn, student, student_id, course_ids)
    conn.close()
    selected = _validate_selection(request.form, course_ids, period_start, period_end, *context)

    return render_maybe_partial(
        "_confirm_panel.html", "confirm.html",
        student=student,
        reason_code=reason_code,
        reason_label=REASON_LABELS[reason_code],
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        selected=selected,
        total_hours=sum(c["periods_missed"] for c in selected),
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

    context = _load_selection_context(conn, student, student_id, course_ids)
    conn.close()  # closed before validation so an abort() here can't leak the connection
    selected = _validate_selection(request.form, course_ids, period_start, period_end, *context)

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

    conn = get_conn()
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


def _search_records(q):
    """Same search-only gate as the home page: with no query, nobody's
    records are listed -- looking someone up requires typing their own
    학번 or 이름 first."""
    if not q:
        return []
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.id, p.created_at, s.student_number, s.name, s.grade, s.class_no,
                  c.course_name, c.professor, p.reason_code,
                  p.class_date, p.class_period_start, p.class_period_end, p.periods_missed
           FROM permit_records p
           JOIN students s ON s.id = p.student_id
           JOIN courses c ON c.id = p.course_id
           WHERE p.term = %s AND (s.student_number LIKE %s OR s.name LIKE %s)
           ORDER BY p.created_at DESC""",
        (TERM, f"%{q}%", f"%{q}%"),
    ).fetchall()
    conn.close()
    return rows


@app.route("/records")
def records():
    q = request.args.get("q", "").strip()
    return render_template("records.html", records=_search_records(q), q=q, reasons=REASON_LABELS, term=TERM)


@app.route("/records/search")
def records_search():
    """Partial-page endpoint the 사용 기록 search box fetches into, mirroring
    /students/search on the home page."""
    q = request.args.get("q", "").strip()
    return render_template("_records_results.html", records=_search_records(q), q=q, reasons=REASON_LABELS)


def _all_records():
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
    return rows


@app.route("/records/admin_view", methods=["POST"])
def records_admin_view():
    """The 사용 기록 tab's 확인 button: entering the admin token here and
    confirming it is what unlocks both the full (unfiltered) record list
    and every delete button -- typing a token without confirming it does
    neither, so viewing everyone's records and deleting both require the
    same real server-side check, not just a non-empty text field."""
    _check_admin_token()
    return render_template("_records_results.html", records=_all_records(), q="", verified=True, reasons=REASON_LABELS)


def _check_admin_token():
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        abort(500, "서버에 ADMIN_TOKEN 환경변수가 설정되어 있지 않습니다. "
                    "Render 서비스의 Environment 설정에 ADMIN_TOKEN을 추가해 주세요.")
    if request.form.get("admin_token") != expected:
        abort(403, "관리자 토큰이 올바르지 않습니다.")


@app.route("/records/delete", methods=["POST"])
def records_delete():
    """Three ways to delete, all admin-token gated: a single row
    (delete_id, the per-row button), several checked rows at once
    (record_ids, the 선택 삭제 button), or every record on file for one
    student this term (delete_student_number, the 이 학생 전체 기록 삭제
    button that appears once a search matches exactly one student)."""
    _check_admin_token()
    conn = get_conn()

    student_number = request.form.get("delete_student_number", "").strip()
    record_ids = [int(i) for i in request.form.getlist("record_ids") if i.strip()]
    single_id = request.form.get("delete_id", "").strip()

    if student_number:
        conn.execute(
            """DELETE FROM permit_records WHERE term=%s AND student_id IN
               (SELECT id FROM students WHERE term=%s AND student_number=%s)""",
            (TERM, TERM, student_number),
        )
    elif record_ids:
        conn.execute("DELETE FROM permit_records WHERE id = ANY(%s)", (record_ids,))
    elif single_id:
        conn.execute("DELETE FROM permit_records WHERE id=%s", (single_id,))

    conn.commit()
    conn.close()
    q = request.form.get("q", "").strip()
    return redirect(url_for("records", q=q) if q else url_for("records"))


@app.route("/admin/import", methods=["GET"])
def admin_import_form():
    return render_template("admin_import.html", term=TERM, result=None)


@app.route("/admin/import", methods=["POST"])
def admin_import():
    """Web version of `python -m app.import_real_data`: upload the school's
    .xls exports straight from the browser and load them into Neon, no
    local Python needed. Files are read into memory and never written to
    disk. Only one file type is picked from the file_type dropdown per
    upload, and only that type's data is touched -- picking 학과별 시간표
    never accidentally clears the roster, for example."""
    _check_admin_token()
    term = request.form.get("term") or TERM
    file_type = request.form.get("file_type", "")

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return render_template("admin_import.html", term=term, result=["업로드할 파일을 선택해 주세요."])

    conn = get_conn()
    log = []
    try:
        if file_type == "roster":
            students = parse_roster(upload.read())
            load_roster(conn, students, term)
            log.append(f"재학생명단: 학생 {len(students)}명 적재 완료")
            conn.commit()

        elif file_type == "timetable":
            grade = request.form.get("grade", "").strip()
            if grade not in {"1", "2", "3", "4"}:
                log.append("학과별 시간표를 올리려면 학년을 선택해 주세요.")
            else:
                slots = parse_timetable(upload.read(), grade)
                load_timetable(conn, slots, term)
                log.append(f"{grade}학년 시간표: 수업 {len(slots)}건 적재 완료")
                conn.commit()

        elif file_type == "makeup":
            makeups = parse_makeup_report(upload.read())
            unmatched = load_course_makeups(conn, makeups, term)
            log.append(f"보강결과보고서: {len(makeups) - len(unmatched)}건 적재 완료")
            if unmatched:
                log.append("다음 항목은 일치하는 과목을 찾지 못해 건너뛰었습니다 (먼저 해당 학과별 "
                            "시간표를 올려주세요): " + "; ".join(unmatched))
            conn.commit()

        else:
            log.append("파일 종류를 선택해 주세요.")
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
