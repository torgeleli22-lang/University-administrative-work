"""Fills the university's 출석인정허가원 (attendance-recognition permit)
.hwpx template with data coming from the database, without touching the
template's design.

A .hwpx file is just a zip archive of XML parts. We only ever edit
Contents/section0.xml (the body text) and copy every other part through
byte-for-byte, so fonts, borders, table layout etc. stay exactly as the
university issued them.

Cell coordinates below were mapped by hand from the original template
(see docs/hwpx-field-map.md) and are specific to this one form.
"""
import zipfile
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from lxml import etree

HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NSMAP = {"hp": HP_NS}
SECTION_PATH = "Contents/section0.xml"

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

REASON_LABELS = {
    1: "1. 국가에서 부과된 의무 이행",
    2: "2. 총장 승인 행사 참여",
    3: "3. 배우자, 본인 및 배우자의 부모 사망",
    4: "4. 본인 및 배우자의 (외)조부모, 자녀, 형제자매 사망",
    5: "5. 본인의 결혼",
    6: "6. 본인의 질병 및 사고",
    7: "7. 본인 및 배우자 출산",
    8: "8. 졸업예정자가 조기 취업이 된 경우",
    9: "9. 기타 총장이 부득이하다고 인정한 경우",
}

# (rowAddr, colAddr) of the checkbox '□' for each reason, inside the
# nested reason-checklist table.
REASON_CHECKBOX_CELLS = {
    1: (1, 0), 2: (2, 0), 3: (3, 0), 4: (4, 0), 5: (5, 0),
    6: (1, 4), 7: (2, 4), 8: (3, 4), 9: (5, 4),
}

# (rowAddr, colAddr) of the value cell for each student-info field, inside
# the main table (the one with rowCnt="14").
INFO_CELLS = {
    "department": (1, 0),
    "grade": (1, 2),
    "student_number": (1, 3),
    "class_no": (1, 6),
    "name": (1, 8),
}

PERIOD_CELL = (3, 1)

# One data row per selected course; the template has 7 blank rows.
COURSE_ROWS = [5, 6, 7, 8, 9, 10, 11]
COURSE_COLS = {"course_name": 1, "professor": 4, "class_day": 5, "class_time": 7}
MAX_COURSES = len(COURSE_ROWS)

APPLY_DATE_PLACEHOLDER = "2026년    월    일"
APPLICANT_PLACEHOLDER = "신  청  자:             (인)"


@dataclass
class PermitRequest:
    student_number: str
    name: str
    department: str
    grade: str
    class_no: str
    reason_code: int
    period_start: date
    period_end: date
    courses: list = field(default_factory=list)  # list of dicts: course_name/professor/class_day/class_time
    apply_date: date = None

    def __post_init__(self):
        if self.apply_date is None:
            self.apply_date = date.today()
        if self.reason_code not in REASON_LABELS:
            raise ValueError(f"Unknown reason_code: {self.reason_code}")
        if len(self.courses) > MAX_COURSES:
            raise ValueError(
                f"Template only has {MAX_COURSES} blank rows for 결석 과목, "
                f"got {len(self.courses)} courses"
            )
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")


def _q(tag):
    return f"{{{HP_NS}}}{tag}"


def _direct_cells(tbl):
    """hp:tc elements that are direct children of this table (skips any
    nested table's cells, which findall('.//hp:tc') would also match)."""
    cells = {}
    for tc in tbl.findall("hp:tr/hp:tc", NSMAP):
        addr = tc.find("hp:cellAddr", NSMAP)
        cells[(int(addr.get("rowAddr")), int(addr.get("colAddr")))] = tc
    return cells


def _set_cell_text(tc, text):
    # Only look at direct paragraphs/runs (a cell may itself contain a
    # nested table, whose runs must not be touched here).
    existing = tc.findall("hp:subList/hp:p/hp:run/hp:t", NSMAP)
    if existing:
        # e.g. the reason checkboxes already hold a '□' text run
        existing[0].text = text
        return
    # Blank cells hold a bare self-closing <hp:run/> with no <hp:t> yet.
    # Some runs instead hold formatting controls (<hp:ctrl>) and must be
    # left alone; use the first run that isn't one of those.
    for run in tc.findall("hp:subList/hp:p/hp:run", NSMAP):
        if run.find("hp:ctrl", NSMAP) is None:
            t = etree.SubElement(run, _q("t"))
            t.text = text
            return
    raise ValueError("No usable text run found in cell")


def _replace_unique_text(tbl_root, original, new_text):
    for t in tbl_root.findall(".//hp:t", NSMAP):
        if t.text == original:
            t.text = new_text
            return
    raise ValueError(f"Could not find expected placeholder text: {original!r}")


def _format_period(start: date, end: date, apply_date: date) -> str:
    days = (end - start).days + 1
    start_wd = WEEKDAYS_KO[start.weekday()]
    end_wd = WEEKDAYS_KO[end.weekday()]
    return (
        f"{apply_date.year}년  {start.month}월  {start.day}일  {start_wd}요일  "
        f"～  {end.month}월  {end.day}일  {end_wd}요일  ( {days}일간 )"
    )


def fill_section_xml(xml_bytes: bytes, req: PermitRequest) -> bytes:
    root = etree.fromstring(xml_bytes)

    tbls = root.findall(".//hp:tbl", NSMAP)
    main_tables = [t for t in tbls if t.get("rowCnt") == "14"]
    if not main_tables:
        raise ValueError("Main table (rowCnt=14) not found — template structure changed?")
    main = main_tables[0]

    main_cells = _direct_cells(main)

    # student info
    values = {
        "department": req.department,
        "grade": req.grade,
        "student_number": req.student_number,
        "class_no": req.class_no,
        "name": req.name,
    }
    for field_name, addr in INFO_CELLS.items():
        _set_cell_text(main_cells[addr], values[field_name])

    # absence period
    _set_cell_text(
        main_cells[PERIOD_CELL],
        _format_period(req.period_start, req.period_end, req.apply_date),
    )

    # courses
    for row, course in zip(COURSE_ROWS, req.courses):
        for field_name, col in COURSE_COLS.items():
            _set_cell_text(main_cells[(row, col)], course.get(field_name, ""))

    # reason checkbox: turn the empty box into a filled one
    reason_tc = main_cells[(2, 1)]
    reason_tbl = reason_tc.find(".//hp:tbl", NSMAP)
    if reason_tbl is None:
        raise ValueError("Reason checklist table not found inside expected cell")
    reason_cells = _direct_cells(reason_tbl)
    checkbox_addr = REASON_CHECKBOX_CELLS[req.reason_code]
    _set_cell_text(reason_cells[checkbox_addr], "■")

    # application date + applicant name (unique free-standing text runs)
    _replace_unique_text(
        main_cells[(12, 0)],
        APPLY_DATE_PLACEHOLDER,
        f"{req.apply_date.year}년   {req.apply_date.month}월   {req.apply_date.day}일",
    )
    _replace_unique_text(
        main_cells[(12, 0)],
        APPLICANT_PLACEHOLDER,
        f"신  청  자: {req.name}         (인)",
    )

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def generate_hwpx(template_path: str, req: PermitRequest) -> bytes:
    """Returns the filled-in .hwpx file as bytes."""
    out_buf = BytesIO()
    with zipfile.ZipFile(template_path, "r") as zin, \
         zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == SECTION_PATH:
                data = fill_section_xml(data, req)
            # preserve each part's original compression (mimetype must stay
            # STORED per the OPC/hwpx spec, and this keeps everything else
            # byte-identical in how it's packed too)
            zout.writestr(item, data, compress_type=item.compress_type)
    return out_buf.getvalue()
