"""무한매수법 V4.0 백테스트 엔진.

근거: 라오어 'V4.0 방법론 1.일반모드 / 2.소진후 리버스모드' (2026.03.14) + 언이 자동계산 시트 스크립트 1.1.

확정된 해석
- 큰수(증권사 주문거부 회피용)는 반영하지 않는다. 첫 매수는 종가에 원금/n 만큼 무조건 매수.
- 아래로 거는 1주씩 추가 LOC 매수는 개수 제한 없음 → 체결되는 날은 1회 매수금으로 종가에 살 수 있는 만큼 산다.
- 지정가 매도: 당일 고가 >= 목표가이면 목표가에 체결(갭 상승도 프리/애프터에서 목표가 체결로 가정).
- 가격은 실제 당시 가격. 전일 종가가 tick_exempt_below($2) 미만이면 매수점 -0.01 규칙을 쓰지 않는다
  (매수점=매도점=별지점, 같은 가격에서 둘 다 해당되면 매도 우선).
- 리버스 종료 조건을 만족해도 T > n-1 이면 종료하지 않고 리버스를 유지한다(발생 횟수 기록).
- 같은 날 LOC 매도가 체결되면 LOC 매수는 체결되지 않는다(가격 구조상 자연스럽게 배타적).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date as _date

from .data import Bar

DEFAULT_TARGET = {"TQQQ": 15.0, "SOXL": 20.0}
NORMAL, REVERSE = "NORMAL", "REVERSE"


@dataclass
class Config:
    ticker: str = "TQQQ"
    n: float = 20                  # 분할수
    target: float | None = None    # 목표수익률(%). None이면 종목 기본값(TQQQ 15, SOXL 20)
    principal: float = 20000.0     # 원금
    start: str | None = None       # 시작일(첫 매수일). None이면 데이터 처음
    end: str | None = None         # 종료일. None이면 데이터 끝
    compound: bool = True          # True=복리(사이클 종료 금액 전부 재투입), False=단리(원금 초과분 인출)
    fee_rate: float = 0.0          # 수수료율(예: 0.0007 = 0.07%). 그날 매수·매도별 체결금액 합계에 적용, 센트 미만 버림
    sec_rate: float = 0.0          # 매도 제세금율(예: 0.0000206). 그날 매도 합계에 적용, 센트 미만 올림
    tick: float = 0.01             # 매수점 = 별지점 - tick
    tick_exempt_below: float = 2.0 # 전일 종가가 이 값 미만이면 tick 미적용

    def target_pct(self) -> float:
        if self.target is not None:
            return float(self.target)
        return DEFAULT_TARGET.get(self.ticker.upper(), 15.0)


@dataclass
class State:
    cash: float
    shares: int = 0
    avg: float = 0.0
    T: float = 0.0
    mode: str = NORMAL
    rev_first: bool = False  # 리버스 1일차(무조건 MOC 매도, 매수 없음) 대기 중


@dataclass
class Orders:
    """하루치 주문표. 전날 장 마감 후의 상태로 만든다."""
    kind: str                     # FIRST / FRONT(전반전) / BACK(후반전) / REV_FIRST / REV / REV_EXHAUST
    amt: float = 0.0              # 1회 매수 시도액
    star_pct: float | None = None # 별%
    star: float | None = None     # 별지점(매도점)
    buys: list = field(default_factory=list)       # (가격, 수량, T증가분, 라벨)
    tier_amt: float = 0.0         # 추가 1주 LOC: 가격 = tier_amt / (tier_base + k), k=1,2,...
    tier_base: int = 0
    loc_sells: list = field(default_factory=list)  # (가격, 수량, 라벨)
    moc_sell: int = 0
    limit_sell: tuple | None = None                # (가격, 수량)

    def lines(self, n_tiers: int = 5) -> list[str]:
        out = []
        for p, q, _, lab in self.buys:
            if q > 0:
                out.append(f"[LOC 매수] ${p:.2f} x {q}주 ({lab})")
        if self.tier_amt > 0:
            for k in range(1, n_tiers + 1):
                out.append(f"[LOC 매수] ${self.tier_amt / (self.tier_base + k):.2f} x 1주 (추가)")
            out.append("  ... (추가 1주 주문 계속)")
        for p, q, lab in self.loc_sells:
            if q > 0:
                out.append(f"[LOC 매도] ${p:.2f} x {q}주 ({lab})")
        if self.moc_sell > 0:
            out.append(f"[MOC 매도] {self.moc_sell}주")
        if self.limit_sell and self.limit_sell[1] > 0:
            out.append(f"[지정가 매도] ${self.limit_sell[0]:.2f} x {self.limit_sell[1]}주")
        return out


def day_fee(gross: float, side: str, rate: float, sec: float) -> float:
    """증권사 정산 방식 수수료. 평단에는 넣지 않고 잔금에만 반영한다."""
    if gross <= 0:
        return 0.0
    base_c = math.floor(gross * rate * 100 + 1e-7)
    tax_c = math.ceil(gross * sec * 100 - 1e-7) if side == "sell" and sec > 0 else 0
    return (base_c + tax_c) / 100


def _r(x: float, tick: float) -> float:
    return round(x, 2) if tick > 0 else round(x, 4)


def star_pct(cfg: Config, T: float) -> float:
    """별% = 목표 - 목표/(n/2) * T  (예: TQQQ 20분할 = 15 - 1.5T)"""
    tgt = cfg.target_pct()
    return tgt - tgt / (cfg.n / 2.0) * T


def make_orders(st: State, cfg: Config, tick: float, sma5: float, capital: float) -> Orders:
    n, half, tgt = cfg.n, cfg.n / 2.0, cfg.target_pct()

    if st.shares == 0:  # 처음 매수: 원금/n, 종가에 무조건
        amt = capital / n
        return Orders(kind="FIRST", amt=amt, tier_amt=amt, tier_base=0)

    if st.mode == NORMAL:
        P = star_pct(cfg, st.T)
        star = _r(st.avg * (1 + P / 100.0), tick)
        amt = st.cash / max(n - st.T, 1e-9)  # 일반모드에서는 T <= n-1 이므로 n-T >= 1
        q4 = st.shares // 4
        o = Orders(kind="FRONT" if st.T < half else "BACK", amt=amt, star_pct=P, star=star)
        o.loc_sells = [(star, q4, "쿼터매도")]
        o.limit_sell = (_r(st.avg * (1 + tgt / 100.0), tick), st.shares - q4)
        pA = max(star - tick, 0.0001)
        if st.T < half:  # 전반전: 절반 별지점 LOC, 절반 평단 LOC
            qA = math.floor(amt * 0.5 / pA)
            pB = _r(st.avg, tick)
            qB = max(0, math.floor(amt / pB) - qA)
            o.buys = [(pA, qA, 0.5, "별지점"), (pB, qB, 0.5, "평단")]
            o.tier_base = qA + qB
        else:            # 후반전: 전액 별지점 LOC
            qA = math.floor(amt / pA)
            o.buys = [(pA, qA, 1.0, "별지점")]
            o.tier_base = qA
        o.tier_amt = amt
        return o

    # REVERSE
    q_sell = math.floor(st.shares / half)  # 20분할=10등분, 40분할=20등분
    if st.rev_first:
        return Orders(kind="REV_FIRST", moc_sell=q_sell)
    star = _r(sma5, tick)  # 직전 5거래일 종가 평균
    amt = st.cash / 4.0    # 쿼터매수
    pB = max(star - tick, 0.0001)
    qB = math.floor(amt / pB)
    if qB <= 0:            # 리버스 중 소진: 1주도 못 사면 MOC 매도
        return Orders(kind="REV_EXHAUST", amt=amt, star=star, moc_sell=q_sell)
    o = Orders(kind="REV", amt=amt, star=star)
    o.buys = [(pB, qB, None, "별지점")]
    o.tier_amt, o.tier_base = amt, qB
    o.loc_sells = [(star, q_sell, "별지점 매도")]
    return o


@dataclass
class Result:
    config: Config
    stats: dict
    cycles: list
    daily: list
    next_orders: Orders | None
    next_state: State | None


def run(cfg: Config, bars: list[Bar], keep_daily: bool = True) -> Result:
    n, tgt = cfg.n, cfg.target_pct()
    fee, sec = cfg.fee_rate, cfg.sec_rate
    idx = [i for i, b in enumerate(bars)
           if (cfg.start is None or b.date >= cfg.start) and (cfg.end is None or b.date <= cfg.end)]
    if not idx:
        raise ValueError("기간 내 데이터가 없습니다")
    i_start, i_end = idx[0], idx[-1]

    st = State(cash=cfg.principal)
    withdrawn = 0.0       # 단리 모드에서 인출한 수익
    closes: deque = deque(maxlen=5)
    daily, cycles = [], []
    cyc = None            # 진행 중 사이클
    peak_eq, mdd, mdd_peak_date, mdd_trough_date, cur_peak_date = cfg.principal, 0.0, None, None, None
    cnt = {"reverse_entries": 0, "blocked_exits": 0, "exhaust_moc": 0, "reverse_days": 0,
           "tick_exempt_days": 0}
    max_T = 0.0

    gross_day = {"buy": 0.0, "sell": 0.0}

    def buy(q: int, price: float):
        cost = q * price
        st.avg = (st.avg * st.shares + cost) / (st.shares + q)
        st.shares += q
        st.cash -= cost  # 수수료는 그날 합계로 따로 뺀다
        gross_day["buy"] += cost

    def sell(q: int, price: float) -> float:
        q = min(q, st.shares)
        gross = q * price
        st.cash += gross
        gross_day["sell"] += gross
        pnl = q * (price - st.avg)
        st.shares -= q
        if st.shares == 0:
            st.avg = 0.0
        return pnl

    for i in range(max(0, i_start - 5), i_end + 1):
        b = bars[i]
        if b.split != 1.0:  # 액면분할: 장 시작 전 수량/평단/과거 종가 보정
            r = b.split
            closes = deque((c / r for c in closes), maxlen=5)
            if st.shares:
                st.shares = int(math.floor(st.shares * r + 1e-9))
                st.avg /= r
        if i < i_start:
            closes.append(b.close)
            continue

        prev_close = closes[-1] if closes else b.open
        tick = cfg.tick if prev_close >= cfg.tick_exempt_below else 0.0
        if tick == 0.0:
            cnt["tick_exempt_days"] += 1
        sma5 = sum(closes) / len(closes) if closes else prev_close
        capital = st.cash
        o = make_orders(st, cfg, tick, sma5, capital)
        mode_before, T_before = st.mode, st.T
        C = b.close
        events, realized = [], 0.0
        gross_day = {"buy": 0.0, "sell": 0.0}
        sold_limit = sold_loc = sold_moc = False

        # 1) 장중 지정가 매도
        if o.limit_sell and o.limit_sell[1] > 0 and b.high >= o.limit_sell[0]:
            p, q = o.limit_sell
            realized += sell(q, p)
            sold_limit = True
            events.append(f"지정가매도 {q}@{p:.2f}")
        # 2) 종가 MOC / LOC 매도
        if o.moc_sell > 0 and st.shares > 0:
            q = min(o.moc_sell, st.shares)
            realized += sell(q, C)
            sold_moc = True
            events.append(f"MOC매도 {q}@{C:.2f}")
        for p, q, lab in o.loc_sells:
            if q > 0 and st.shares > 0 and C >= p:
                q = min(q, st.shares)
                realized += sell(q, C)
                sold_loc = True
                events.append(f"LOC매도({lab}) {q}@{C:.2f}")
        # 3) 종가 LOC 매수 (LOC 매도가 체결된 날은 없음)
        bought, t_inc = 0, 0.0
        if not sold_loc and o.kind not in ("REV_FIRST", "REV_EXHAUST"):
            for p, q, inc, lab in o.buys:
                if q > 0 and C <= p:
                    bought += q
                    t_inc += inc or 0.0
            if o.tier_amt > 0 and C <= o.tier_amt / (o.tier_base + 1):
                bought += math.floor(o.tier_amt / C) - o.tier_base
            affordable = math.floor(st.cash / (C * (1 + fee))) if C > 0 else 0
            bought = max(0, min(bought, affordable))
            if bought > 0:
                buy(bought, C)
                events.append(f"LOC매수 {bought}@{C:.2f}")
        # 3-1) 그날 매수·매도별 합계로 수수료 정산
        fee_b = day_fee(gross_day["buy"], "buy", fee, sec)
        fee_s = day_fee(gross_day["sell"], "sell", fee, sec)
        st.cash -= fee_b + fee_s
        realized -= fee_b + fee_s

        # 4) T 갱신
        if o.kind == "FIRST":
            if bought > 0:
                st.T = 1.0
                st.mode = NORMAL
                cyc = {"start": b.date, "start_capital": capital, "max_T": 1.0, "reverse_entries": 0,
                       "days": 0, "peak": capital, "mdd": 0.0, "low_ret": 0.0}
        elif o.kind in ("FRONT", "BACK"):
            if bought > 0 and t_inc == 0.0:
                t_inc = 1.0  # 본 주문 수량이 0이고 추가 1주만 체결된 경우
            if sold_limit:
                st.T = st.T * 0.25 + t_inc       # 지정가 매도 후 LOC 매수: x0.25 + 1 / +0.5
            elif sold_loc:
                st.T = st.T * 0.75               # 쿼터매도
            else:
                st.T += t_inc
        else:  # 리버스
            if (sold_moc or sold_loc):
                st.T *= (1 - 2.0 / n)            # 20분할 x0.9, 40분할 x0.95
            if bought > 0:
                st.T += (n - st.T) * 0.25
            if o.kind == "REV_EXHAUST":
                cnt["exhaust_moc"] += 1
            if o.kind == "REV_FIRST":
                st.rev_first = False

        # 5) 모드 전환 / 사이클 종료
        cycle_end = (st.shares == 0 and o.kind != "FIRST" and (sold_limit or sold_loc or sold_moc))
        if cycle_end:
            end_cash = st.cash
            cyc["days"] += 1
            cyc.update(end=b.date, end_cash=end_cash, profit=end_cash - cyc["start_capital"],
                       ret_pct=(end_cash / cyc["start_capital"] - 1) * 100, final_T=T_before)
            cycles.append(cyc)
            cyc = None
            st.T, st.mode, st.rev_first = 0.0, NORMAL, False
            if not cfg.compound and st.cash > cfg.principal:
                withdrawn += st.cash - cfg.principal
                st.cash = cfg.principal
            events.append("사이클 종료")
        elif st.shares > 0:
            if st.mode == NORMAL and st.T > n - 1:
                st.mode, st.rev_first = REVERSE, True
                cnt["reverse_entries"] += 1
                cyc["reverse_entries"] += 1
                events.append("리버스 진입")
            elif st.mode == REVERSE and C > st.avg * (1 - tgt / 100.0):
                if st.T > n - 1:
                    cnt["blocked_exits"] += 1
                    events.append("리버스 종료조건 충족했으나 T>n-1로 유지")
                else:
                    st.mode = NORMAL
                    events.append("리버스 종료→일반모드")

        if mode_before == REVERSE:
            cnt["reverse_days"] += 1
        max_T = max(max_T, st.T)
        closes.append(C)

        equity = st.cash + st.shares * C + withdrawn
        if equity > peak_eq:
            peak_eq, cur_peak_date = equity, b.date
        dd = equity / peak_eq - 1
        if dd < mdd:
            mdd, mdd_peak_date, mdd_trough_date = dd, cur_peak_date, b.date
        if cyc is not None:
            cyc["days"] += 1
            cyc["max_T"] = max(cyc["max_T"], st.T)
            ceq = st.cash + st.shares * C
            cyc["peak"] = max(cyc["peak"], ceq)
            cyc["mdd"] = min(cyc["mdd"], ceq / cyc["peak"] - 1)
            cyc["low_ret"] = min(cyc["low_ret"], ceq / cyc["start_capital"] - 1)

        if keep_daily:
            daily.append({
                "date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": C,
                "order_kind": o.kind, "mode": st.mode, "T": st.T, "T_before": T_before,
                "star_pct": o.star_pct, "star": o.star, "amt": o.amt,
                "shares": st.shares, "avg": st.avg, "cash": st.cash, "withdrawn": withdrawn,
                "equity": equity, "drawdown": dd, "realized": realized, "events": " / ".join(events),
            })

    # 결과 집계
    last = bars[i_end]
    first_date = bars[i_start].date
    years = max((_date.fromisoformat(last.date) - _date.fromisoformat(first_date)).days / 365.25, 1e-9)
    final_eq = st.cash + st.shares * last.close + withdrawn
    wins = [c for c in cycles if c["profit"] > 0]
    stats = {
        "ticker": cfg.ticker.upper(), "n": cfg.n, "target": tgt, "compound": cfg.compound,
        "fee_rate": fee, "sec_rate": sec, "start": first_date, "end": last.date, "years": round(years, 3),
        "principal": cfg.principal, "final_equity": round(final_eq, 2),
        "total_return_pct": round((final_eq / cfg.principal - 1) * 100, 2),
        "cagr_pct": round(((final_eq / cfg.principal) ** (1 / years) - 1) * 100, 2) if final_eq > 0 else -100.0,
        "mdd_pct": round(mdd * 100, 2), "mdd_peak": mdd_peak_date, "mdd_trough": mdd_trough_date,
        "cycles": len(cycles), "win_cycles": len(wins),
        "avg_cycle_days": round(sum(c["days"] for c in cycles) / len(cycles), 1) if cycles else None,
        "max_cycle_days": max((c["days"] for c in cycles), default=None),
        "avg_cycle_ret_pct": round(sum(c["ret_pct"] for c in cycles) / len(cycles), 2) if cycles else None,
        "worst_cycle_ret_pct": round(min((c["ret_pct"] for c in cycles)), 2) if cycles else None,
        "worst_cycle_low_pct": round(min([c["low_ret"] for c in cycles] + ([cyc["low_ret"]] if cyc else [0])) * 100, 2),
        "max_T": round(max_T, 4),
        "reverse_entries": cnt["reverse_entries"], "reverse_days": cnt["reverse_days"],
        "blocked_exits": cnt["blocked_exits"], "exhaust_moc": cnt["exhaust_moc"],
        "tick_exempt_days": cnt["tick_exempt_days"],
        "open_cycle": cyc is not None, "open_mode": st.mode if cyc else None,
        "open_T": round(st.T, 4) if cyc else None,
        "open_days": cyc["days"] if cyc else None,
        "open_unrealized_pct": round((st.cash + st.shares * last.close) / cyc["start_capital"] * 100 - 100, 2) if cyc else None,
    }
    tick = cfg.tick if last.close >= cfg.tick_exempt_below else 0.0
    sma5 = sum(closes) / len(closes)
    nxt = make_orders(st, cfg, tick, sma5, st.cash)
    return Result(cfg, stats, cycles, daily, nxt, st)
