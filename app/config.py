"""Shared constants. Update TERM every semester before re-importing data."""

TERM = "2026-2"

WEEKDAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

# A course may only be used as the reason for an 출석인정허가원 once per
# term ("한 과목 및 수업 당 2회 이상 사용 불가" -> at most 1 use allowed).
MAX_USES_PER_COURSE = 1
