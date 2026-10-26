# 급등스캐너 (주식 AI 사이트 키트 · 급등·단타형)

- 사이트 설정: `config.json`
- 고유 기능: 급등주(`/surge/`, +15% 이상·상한가), 거래량 폭발(`/volume-spike/`, 평소 10배 이상),
  연속 상승 일수 순위(`/streak/`), 메인 첫 화면 '급등 TOP 10' 카드, 종목 페이지 '단기 레이더'(상승률 순위·거래량 배수·연속 일수·장중 고가),
  숫자 먼저 쓰는 AI 단기 코멘트, 매일 급등·거래량 폭발·연속 상승 리포트
- 로컬 미리보기: `DEMO_NEWS=1 python build.py --demo` → `python -m http.server 8000 --directory site`
- 배포 전 `site/`, `posts/` 폴더는 지우세요 (데모 데이터).
