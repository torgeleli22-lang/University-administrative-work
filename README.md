# 출석인정허가원 자동생성

학생 정보/수강 과목을 DB에서 불러와 신구대학교 출석인정허가원(.hwpx) 서식에
자동으로 채워 넣고 다운로드하는 작은 웹앱입니다. 원본 한글 서식의 디자인/서식은
전혀 건드리지 않고, 표 안의 빈 칸에 내용만 채워 넣습니다.

## 실행 방법

```bash
pip install -r requirements.txt
python -m app.seed          # 샘플 학생/과목 데이터로 DB 생성 (data/attendance.db)
python -m flask --app app.app run
```

브라우저에서 http://127.0.0.1:5000 접속 → 학생 검색/선택 → 결석 사유·기간·과목
선택 → "한글(.hwpx) 파일로 다운로드" 클릭.

## 실제 데이터 연결하기

`app/seed.py`의 `STUDENTS` / `COURSES` / `ENROLLMENTS`는 예시 데이터입니다.
실제 학사 데이터(학생/과목/수강 정보)를 CSV나 엑셀로 내보낼 수 있으면, 그 파일을
읽어서 `app/db.py`의 `students` / `courses` / `enrollments` 테이블에 넣는
가져오기 스크립트로 교체하면 됩니다. 스키마는 `app/db.py`를 참고하세요.

## 구조

- `app/db.py` — SQLite 스키마 (students, courses, enrollments)
- `app/seed.py` — 샘플 데이터 삽입 스크립트
- `app/hwpx_filler.py` — hwpx 템플릿을 열어 표 셀에 값을 채우고 다시 압축하는 핵심 로직
- `app/app.py` — Flask 라우트 (학생 목록/검색, 작성 폼, 생성+다운로드)
- `app/templates/`, `app/static/` — 화면
- `app/assets/attendance_permit_template.hwpx` — 학교 원본 서식 (수정 금지)
- `docs/hwpx-field-map.md` — hwpx 표 좌표와 필드 매핑 문서 (서식이 바뀌면 참고)

## 알아둘 점

- 결석 과목은 서식에 있는 빈 줄 수만큼(현재 7개)까지만 채울 수 있습니다. 그 이상
  선택하면 생성 시 오류가 납니다.
- 지도교수/학과장 결재란은 실제 서명/날인용이라 자동으로 채우지 않습니다.
- 신청일자는 항상 오늘 날짜로 채워집니다.
