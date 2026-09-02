"""Shared constants. Update TERM every semester before re-importing data."""

TERM = "2026-2"

WEEKDAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

# A course may only be used as the reason for an 출석인정허가원 once per
# term ("한 과목 및 수업 당 2회 이상 사용 불가" -> at most 1 use allowed).
MAX_USES_PER_COURSE = 1

# Within a single permit, a course's total missed class-hours over the
# 결석 기간 can't reach 12 ("12시간 이상 사용 불가"): a course meeting
# periods 1-3 is "3시간", so 4 such sessions (12시간) is already too many,
# but 3 sessions plus 2 more periods (11시간) is still fine. This counts
# periods (교시), not clock hours, per the school's own usage.
MAX_ABSENCE_PERIODS_PER_COURSE = 11
