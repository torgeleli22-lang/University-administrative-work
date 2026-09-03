"""Shared constants. Update TERM every semester before re-importing data."""

TERM = "2026-2"

WEEKDAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

# A course's cumulative total across every 출석인정허가원 a student has ever
# been issued for it (this term) can't reach 12 hours ("12시간 이상 사용
# 불가", 1교시 = 1시간): e.g. two permits of 6시간 each is fine (12 total is
# not -- the cap is a strict "less than 12"), so this is the largest total
# still allowed. Every time a permit is generated the exact 교시 range used
# is logged to permit_records, and new requests are checked against the sum
# of everything already on file for that student+course.
MAX_ABSENCE_PERIODS_PER_COURSE = 11

# 교시 select boxes (수업시간) offer this whole range regardless of a
# course's normal schedule, since the office may need to record a
# different range than the course's usual slot.
PERIOD_CHOICES = list(range(1, 13))

# 선택과목 (electives): the timetable file ties every course to one 분반
# (section) via its "who's enrolled" column, which is correct for 전공필수
# courses (the whole homeroom takes them together) but wrong for electives
# -- students choose one of several parallel offerings and the 분반 letter
# on each offering is really just an elective-group label, not the
# student's actual homeroom. Any course whose name is listed here is shown
# to every student in its grade regardless of section, in addition to
# students in its own listed section. Update this each term as electives
# change (there is no marker for "이 과목은 선택과목이다" in the timetable
# file itself, so this has to be maintained by hand).
ELECTIVE_COURSE_NAMES = {"애플리케이션프레임워크", "빅데이터프로그래밍"}
