from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from app.db import get_conn, init_db
from app.hwpx_filler import MAX_COURSES, REASON_LABELS, PermitRequest, generate_hwpx
from io import BytesIO

TEMPLATE_HWPX = Path(__file__).resolve().parent / "assets" / "attendance_permit_template.hwpx"

app = Flask(__name__)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    conn = get_conn()
    if q:
        rows = conn.execute(
            """SELECT * FROM students
               WHERE student_number LIKE ? OR name LIKE ?
               ORDER BY student_number""",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM students ORDER BY student_number").fetchall()
    conn.close()
    return render_template("index.html", students=rows, q=q)


@app.route("/student/<int:student_id>")
def student_form(student_id):
    conn = get_conn()
    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if student is None:
        conn.close()
        abort(404)
    courses = conn.execute(
        """SELECT c.* FROM courses c
           JOIN enrollments e ON e.course_id = c.id
           WHERE e.student_id = ?
           ORDER BY c.course_name""",
        (student_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "form.html",
        student=student,
        courses=courses,
        reasons=REASON_LABELS,
        max_courses=MAX_COURSES,
        today=date.today().isoformat(),
    )


@app.route("/student/<int:student_id>/generate", methods=["POST"])
def generate(student_id):
    conn = get_conn()
    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if student is None:
        conn.close()
        abort(404)

    reason_code = int(request.form["reason_code"])
    period_start = datetime.strptime(request.form["period_start"], "%Y-%m-%d").date()
    period_end = datetime.strptime(request.form["period_end"], "%Y-%m-%d").date()

    course_ids = [int(cid) for cid in request.form.getlist("course_ids")]
    courses = []
    if course_ids:
        placeholders = ",".join("?" for _ in course_ids)
        rows = conn.execute(
            f"""SELECT c.* FROM courses c
                JOIN enrollments e ON e.course_id = c.id
                WHERE e.student_id = ? AND c.id IN ({placeholders})
                ORDER BY c.course_name""",
            (student_id, *course_ids),
        ).fetchall()
        courses = [dict(r) for r in rows]
    conn.close()

    if len(courses) > MAX_COURSES:
        abort(400, f"결석 과목은 최대 {MAX_COURSES}개까지 선택할 수 있습니다.")

    req = PermitRequest(
        student_number=student["student_number"],
        name=student["name"],
        department=student["department"],
        grade=student["grade"],
        class_no=student["class_no"],
        reason_code=reason_code,
        period_start=period_start,
        period_end=period_end,
        courses=courses,
    )

    data = generate_hwpx(str(TEMPLATE_HWPX), req)
    filename = f"출석인정허가원_{student['name']}_{period_start.isoformat()}.hwpx"
    return send_file(
        BytesIO(data),
        as_attachment=True,
        download_name=filename,
        mimetype="application/haansofthwpx",
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
