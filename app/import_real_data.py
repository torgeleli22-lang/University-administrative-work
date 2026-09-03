"""Import real 학사 데이터(.xls) exported from the school system into the DB.

Two kinds of files are expected, both in the old .xls (BIFF) format the
학사 시스템 exports:

  * 재학생명단 (roster) -- one file, all grades/sections, containing
    repeated blocks like:
        재  학  생  명  단
        학과 : 컴퓨터소프트웨어과   학년 : 1   반 : A
        순번 학번 ... 이름 ... 성별 주민등록번호 ... 전형구분 ... 비고
        <student rows...>

  * 학과별 전체시간표 (timetable) -- one file per grade, a grid of
    교시(period) x 요일(day) where each cell holds one or more lines like
    "A 데이터베이스기초 정재헌 남관 212" (분반/section, 과목명, 교수명,
    건물, 강의실). Multiple sections in one cell are newline-separated.
    This alone tells us every course offered this term, who teaches it,
    and which 교시 it meets -- no separate 강의계획서 file is needed.

We deliberately do NOT store 주민등록번호 (resident registration number) --
the form never needs it and there is no reason to keep even the partially
masked version around.

Run:
    python -m app.import_real_data \\
        --roster PATH --timetable GRADE:PATH [--timetable GRADE:PATH ...]

None of the source .xls files or the populated database should be committed
to git -- see .gitignore and README.md.
"""
import argparse
import re
import sys

import xlrd

from app.config import TERM, WEEKDAY_ORDER
from app.db import get_conn, init_db

ROSTER_BLOCK_HEADER = "재  학  생  명  단"
BLOCK_INFO_RE = re.compile(r"학과\s*:\s*(\S+)\s+학년\s*:\s*(\d+)\s+반\s*:\s*(\S+)")

DAY_NAMES = set(WEEKDAY_ORDER)


def _cell_str(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


# ---------------------------------------------------------------------------
# 재학생명단 (roster)
# ---------------------------------------------------------------------------

def parse_roster(file_bytes):
    """Yields dicts: student_number, name, department, grade, class_no."""
    book = xlrd.open_workbook(file_contents=file_bytes)
    sh = book.sheet_by_index(0)

    students = []
    r = 0
    while r < sh.nrows:
        row = sh.row_values(r)
        if row and _cell_str(row[0]) == ROSTER_BLOCK_HEADER:
            info_row = sh.row_values(r + 1)
            m = BLOCK_INFO_RE.search(_cell_str(info_row[1]))
            if not m:
                raise ValueError(f"Could not parse block header at row {r + 1}: {info_row[1]!r}")
            department, grade, class_no = m.group(1), m.group(2), m.group(3)
            r += 3  # skip block title, info line, column header line
            while r < sh.nrows:
                data_row = sh.row_values(r)
                if not data_row or _cell_str(data_row[0]) == ROSTER_BLOCK_HEADER:
                    break
                student_number = _cell_str(data_row[2])
                name = _cell_str(data_row[6])
                if not student_number or not name:
                    break
                students.append({
                    "student_number": student_number,
                    "name": name,
                    "department": department,
                    "grade": grade,
                    "class_no": class_no,
                })
                r += 1
        else:
            r += 1
    return students


# ---------------------------------------------------------------------------
# 학과별 전체시간표 (timetable)
# ---------------------------------------------------------------------------

def _parse_cell_line(line):
    """'A 데이터베이스기초 정재헌 남관 212' -> dict, or None if not a real entry."""
    tokens = line.split()
    if len(tokens) < 4 or not (len(tokens[0]) == 1 and tokens[0].isalpha()):
        return None
    section, room, building, professor = tokens[0], tokens[-1], tokens[-2], tokens[-3]
    course_name = " ".join(tokens[1:-3])
    if not course_name:
        return None
    return {
        "section": section,
        "course_name": course_name,
        "professor": professor,
        "building": building,
        "room": room,
    }


def parse_timetable(file_bytes, grade):
    """Yields dicts: grade, section, course_name, professor, day,
    period_start, period_end, building, room -- one row per class meeting
    (already consolidated across consecutive periods)."""
    book = xlrd.open_workbook(file_contents=file_bytes)
    sh = book.sheet_by_index(0)

    header_row = None
    for r in range(sh.nrows):
        if _cell_str(sh.cell_value(r, 0)) == "교시":
            header_row = r
            break
    if header_row is None:
        raise ValueError(f"{grade}학년 시간표: could not find the '교시' header row")

    day_cols = {}
    for c in range(sh.ncols):
        val = _cell_str(sh.cell_value(header_row, c))
        if val in DAY_NAMES:
            day_cols[c] = val

    # raw[(section, course_name, professor, building, room, day)] = set(periods)
    raw = {}
    for r in range(header_row + 1, sh.nrows):
        period_cell = _cell_str(sh.cell_value(r, 0))
        if not period_cell.isdigit():
            continue
        period = int(period_cell)
        for c, day in day_cols.items():
            cell = sh.cell_value(r, c)
            if not isinstance(cell, str) or not cell.strip():
                continue
            for line in cell.split("\n"):
                entry = _parse_cell_line(line.strip())
                if entry is None:
                    continue
                key = (entry["section"], entry["course_name"], entry["professor"],
                       entry["building"], entry["room"], day)
                raw.setdefault(key, set()).add(period)

    # group by (section, course_name, professor) -> list of (day, periods)
    grouped = {}
    for (section, course_name, professor, building, room, day), periods in raw.items():
        grouped.setdefault((section, course_name, professor), []).append(
            (day, sorted(periods), building, room)
        )

    results = []
    for (section, course_name, professor), day_slots in grouped.items():
        day_slots.sort(key=lambda ds: WEEKDAY_ORDER.index(ds[0]))
        for day, periods, building, room in day_slots:
            # consolidate consecutive period numbers into ranges
            start = periods[0]
            prev = periods[0]
            for p in periods[1:] + [None]:
                if p is not None and p == prev + 1:
                    prev = p
                    continue
                results.append({
                    "grade": grade,
                    "section": section,
                    "course_name": course_name,
                    "professor": professor,
                    "day": day,
                    "period_start": start,
                    "period_end": prev,
                    "building": building,
                    "room": room,
                })
                if p is not None:
                    start = prev = p
    return results


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

def load_roster(conn, students, term):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM students WHERE term = %s", (term,))
        cur.executemany(
            """INSERT INTO students (term, student_number, name, department, grade, class_no)
               VALUES (%(term)s, %(student_number)s, %(name)s, %(department)s, %(grade)s, %(class_no)s)""",
            [{**s, "term": term} for s in students],
        )


def load_timetable(conn, slots, term):
    # Neon is a remote DB -- every query is a network round trip, and a
    # timetable easily has 50+ slots, so this used to do INSERT + SELECT +
    # INSERT per slot (150+ round trips) and blew past gunicorn's request
    # timeout. ON CONFLICT ... DO UPDATE ... RETURNING id gets the course's
    # id in the same round trip as the upsert (a plain DO NOTHING doesn't
    # return anything on conflict), and the course_slots rows are collected
    # and sent as a single executemany instead of one INSERT each.
    cur = conn.cursor()
    course_ids = {}
    for slot in slots:
        key = (slot["grade"], slot["section"], slot["course_name"], slot["professor"])
        if key not in course_ids:
            cur.execute(
                """INSERT INTO courses (term, grade, section, course_name, professor)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (term, grade, section, course_name, professor)
                   DO UPDATE SET term = excluded.term
                   RETURNING id""",
                (term, *key),
            )
            course_ids[key] = cur.fetchone()["id"]

    # ON CONFLICT here (course_slots_unique_idx, see db.py) makes re-running
    # the same timetable import a no-op instead of doubling up every slot.
    cur.executemany(
        """INSERT INTO course_slots (course_id, day, period_start, period_end, building, room)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (course_id, day, period_start, period_end)
           DO UPDATE SET building = excluded.building, room = excluded.room""",
        [
            (course_ids[(s["grade"], s["section"], s["course_name"], s["professor"])],
             s["day"], s["period_start"], s["period_end"], s["building"], s["room"])
            for s in slots
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--roster", required=True, help="재학생명단 .xls path")
    parser.add_argument("--timetable", action="append", default=[], metavar="GRADE:PATH",
                         help="e.g. --timetable 1:파일.xls (repeatable, one per grade)")
    parser.add_argument("--term", default=TERM)
    args = parser.parse_args()

    init_db()
    conn = get_conn()

    with open(args.roster, "rb") as f:
        students = parse_roster(f.read())
    load_roster(conn, students, args.term)
    print(f"학생 {len(students)}명 적재 완료")

    total_slots = 0
    for spec in args.timetable:
        grade, path = spec.split(":", 1)
        with open(path, "rb") as f:
            slots = parse_timetable(f.read(), grade)
        load_timetable(conn, slots, args.term)
        total_slots += len(slots)
        print(f"{grade}학년 시간표: 수업 {len(slots)}건 적재 완료 ({path})")
    print(f"총 수업 슬롯 {total_slots}건")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
