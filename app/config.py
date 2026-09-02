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
