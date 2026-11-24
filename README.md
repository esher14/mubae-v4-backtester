# 무매 V4 백테스터

라오어 님의 무한매수법 V4.0 규칙으로 TQQQ·SOXL 일봉 백테스트를 돌리는 웹 페이지와 Python 도구입니다.
규칙 자체는 원문([일반모드](https://cafe.naver.com/infinitebuying/79263), [소진후 리버스모드](https://cafe.naver.com/infinitebuying/79264))을 참고하세요.

과거 데이터로 계산한 참고용 결과이며 투자 권유가 아닙니다.

## 웹 페이지 (`app/`)

- `app/index.html`: 화면. 종목·분할수·목표수익률·기간·원금을 넣으면 기간 수익률, MDD, 사이클 종료 시점, 다음 거래일 주문표를 보여줍니다.
  종목 단순 보유·QQQ 단순 보유와 평가금·낙폭을 비교하고, 사이클을 누르면 날짜별 체결 내역과 T·평단·별지점 흐름을 볼 수 있습니다.
- `app/tracker.html`: 실제 매매 기록장. 체결을 날짜별로 기록하면 T·평단·잔금·모드와 다음 거래일 주문표를 계산하고,
  평가금 추이·사이클 기록·규칙과 다르게 체결된 날·같은 조건 백테스트와의 비교를 보여줍니다. 보유 주식과 현금으로 중간 진입할 수 있습니다.
  **기록은 사용하는 브라우저(localStorage)에만 저장되고 서버로 전송되지 않습니다.** 기기를 옮기거나 보관하려면 백업 파일(JSON)을 저장·불러오세요.
- `app/tracker-core.js`: 기록장 계산부. 주문은 `engine.js`의 `makeOrders`를 그대로 쓰고, 기록한 체결로 상태를 다시 계산합니다.
  엔진이 가정한 체결을 그대로 기록하면 엔진과 같은 결과가 나오는지 `test_tracker.html`로 검증합니다(저장소 루트에서 `python -m http.server` 후 브라우저로 열기).
- `app/engine.js`: 백테스트 엔진(브라우저용). `mubae/engine.py`와 같은 결과를 내는지 `python test_js_engine.py`로 검증합니다.
- `app/data/*.csv`: 가격 데이터. GitHub Actions(`.github/workflows/update-and-deploy.yml`)가 평일 매일 오전 8시 17분(한국시간)에
  `scripts/fetch_prices.py`로 Yahoo Finance 일봉을 받아 새로 만들고 GitHub Pages에 배포합니다.
  저장소에 커밋된 CSV는 받기에 실패했을 때 쓰는 비상용 사본입니다.

## Python 도구

```powershell
python test_rules.py                                              # 규칙 검증
python backtest.py --ticker TQQQ --n 20 --start 2020-01-01 --end 2023-12-31
python sweep.py --horizon 3                                       # 종목 x 분할 x 시작월 스윕
```

## 백테스트 가정

| 항목 | 처리 |
|---|---|
| 가격 | Yahoo Finance 일봉을 액면분할 복원한 실제 당시 가격. 배당 미반영 |
| 추가 LOC 매수 | 개수 제한 없이 건 것으로 계산. 큰수 매수 미반영 |
| 지정가 매도 | 당일 고가 >= 목표가이면 목표가 체결(갭 상승도 목표가 체결로 가정) |
| 매수점 -0.01 | 전일 종가 $2 미만이면 적용하지 않음 |
| 리버스 종료 | 종료 조건을 만족해도 소진 상태(T > 분할수-1)이면 리버스 유지 |
| 20·40 이외 분할수 | 20·40분할 공식을 분할수에 비례해 일반화 |
| 사이클 종료 후 | 다음 거래일 재시작. 복리(기본) 또는 단리(원금 초과분 인출, 평가금에는 포함) |
| 수익률·MDD | 일별 평가금(현금 + 보유수량 x 종가) 기준, 미실현 손익 포함 |
