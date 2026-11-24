"""방법론 글에 나온 예시 숫자로 엔진 규칙을 검증한다.  실행: python test_rules.py"""
import math

from mubae.data import Bar
from mubae.engine import Config, State, make_orders, run, star_pct, NORMAL, REVERSE

fails = 0


def check(name, got, want):
    global fails
    ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-6
    print(("OK  " if ok else "FAIL"), name, "got", got, "want", want)
    if not ok:
        fails += 1


# 1. 별% 공식 / 별지점: SOXL 20분할, 평단 38.30, T=8.6 → 별% 2.8%, 별지점 39.37
c = Config(ticker="SOXL", n=20)
check("SOXL20 별%", round(star_pct(c, 8.6), 6), 2.8)
check("SOXL20 별지점", round(38.30 * (1 + star_pct(c, 8.6) / 100), 2), 39.37)
check("TQQQ40 별%(T=10)", star_pct(Config(ticker="TQQQ", n=40), 10), 7.5)

# 2. 1회 매수금: 2만달러 40분할, 첫 매수 478 사용 → 2회차 19522/39
st = State(cash=20000 - 478, shares=10, avg=47.8, T=1.0)
o = make_orders(st, Config(ticker="TQQQ", n=40), 0.01, 0, 0)
check("2회차 매수시도액", round(o.amt, 4), round(19522 / 39, 4))

# 3. 첫 매수: 1회 617.89$, 다음날 종가 44 → 14개 (12개 + 47.53 1개 + 44.13 1개)
o = make_orders(State(cash=617.89 * 20), Config(n=20), 0.01, 0, 617.89 * 20)
check("첫매수 시도액", round(o.amt, 2), 617.89)
check("첫매수 체결수량(종가44)", math.floor(o.amt / 44), 14)
check("첫매수 추가주문가 47.53", round(o.amt / 13, 2), 47.53)

# 4. 전반전 주문표: 별지점 78.12, 평단 69.75, 1회 539.2$ → 78.11x3, 69.75x4, 67.40x1, 59.91x1
#    (TQQQ 40분할, T를 역산해 별지점 78.12가 되도록 설정)
cfg = Config(ticker="TQQQ", n=40)
avg = 69.75
P = (78.12 / avg - 1) * 100
T = (15 - P) / 0.75
st = State(cash=539.2 * (40 - T), shares=100, avg=avg, T=T)
o = make_orders(st, cfg, 0.01, 0, 0)
check("전반전 별지점", o.star, 78.12)
check("전반전 별LOC", (round(o.buys[0][0], 2), o.buys[0][1]), (78.11, 3))
check("전반전 평단LOC", (o.buys[1][0], o.buys[1][1]), (69.75, 4))
check("전반전 추가1", round(o.tier_amt / (o.tier_base + 1), 2), 67.40)
check("전반전 추가2", round(o.tier_amt / (o.tier_base + 2), 2), 59.91)

# 5. 후반전 주문표: 별지점 59.55, 1회 약 568.5$ → 59.54x9, 56.85, 51.68, 47.37
avg = 70.0
P = (59.55 / avg - 1) * 100
T = (15 - P) / 0.75
st = State(cash=568.47 * (40 - T), shares=141, avg=avg, T=T)  # 56.85/51.68/47.37를 모두 만족하는 금액
o = make_orders(st, cfg, 0.01, 0, 0)
check("후반전 kind", o.kind, "BACK")
check("후반전 별LOC", (round(o.buys[0][0], 2), o.buys[0][1]), (59.54, 9))
check("후반전 추가", [round(o.tier_amt / (o.tier_base + k), 2) for k in (1, 2, 3)], [56.85, 51.68, 47.37])
# 매도표: 141개 → LOC 35개, 지정가 106개
check("쿼터매도 수량", o.loc_sells[0][1], 35)
check("지정가 수량", o.limit_sell[1], 106)

# 6. 리버스 매도수량: 40분할 200개 → 10, 190→9, 181→9, 172→8 ; 198개 20분할→19
seq, s = [], 200
for _ in range(4):
    q = math.floor(s / 20)
    seq.append(q)
    s -= q
check("리버스 매도수량 40분할", seq, [10, 9, 9, 8])
o = make_orders(State(cash=0, shares=198, avg=50, T=19.5, mode=REVERSE, rev_first=True), Config(n=20), 0.01, 0, 0)
check("리버스 1일차 MOC 20분할 198개", (o.kind, o.moc_sell), ("REV_FIRST", 19))

# 7. 리버스 T: 40분할 T=39.5 → 매도 37.525 → 매수 38.14375
T = 39.5 * (1 - 2 / 40)
check("리버스 매도 T", round(T, 6), 37.525)
check("리버스 매수 T", round(T + (40 - T) * 0.25, 6), 38.14375)

# 8. 리버스 쿼터매수액: 잔금 400 + 매도금 300 → 175
o = make_orders(State(cash=700, shares=190, avg=50, T=37.525, mode=REVERSE), Config(n=40), 0.01, 30.0, 0)
check("리버스 쿼터매수액", o.amt, 175.0)
check("리버스 별지점=5일평균", o.star, 30.0)

# 9. 통합 시뮬레이션(합성 가격): 지정가 매도 후 LOC 매수 → T = x0.25 + 1
def bar(d, o, h, l, c):
    return Bar(d, o, h, l, c)
bars = [bar("2020-01-01", 100, 100, 100, 100),
        bar("2020-01-02", 100, 100, 95, 95),     # 전반전 평단 아래 → +1
        bar("2020-01-03", 95, 120, 90, 90)]      # 장중 +15% 도달(지정가 매도) 후 종가 급락 → LOC 매수
r = run(Config(ticker="TQQQ", n=20, principal=20000), bars)
d = r.daily
check("1일차 T", d[0]["T"], 1.0)
check("2일차 T", d[1]["T"], 2.0)
check("3일차 T (x0.25+1)", d[2]["T"], 2.0 * 0.25 + 1.0)
check("3일차 이벤트에 지정가매도", "지정가매도" in d[2]["events"], True)

# 10. 사이클 종료: 지정가+쿼터 동시 체결
bars = [bar("2020-01-01", 100, 100, 100, 100),
        bar("2020-01-02", 100, 120, 100, 116)]
r = run(Config(ticker="TQQQ", n=20, principal=20000), bars)
check("사이클 종료", r.stats["cycles"], 1)
check("사이클 종료 후 보유", r.daily[-1]["shares"], 0)

print("\n전체 결과:", "모두 통과" if fails == 0 else f"{fails}건 실패")
raise SystemExit(1 if fails else 0)
