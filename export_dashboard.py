"""대시보드용 데이터 내보내기.  실행: python export_dashboard.py

dashboard/ 폴더에 세 개의 JSON({rows, meta})을 만든다.
- sweep_runs.json   : 종목 x 분할(20/30/40) x 목표(10/15/20/25%) x 시작월(2011-01부터 매월), 각 3년 운용 결과
- equity_curves.json: 2011-01-03 시작 ~ 데이터 끝까지 주간 평가금/낙폭 (조합별 + 단순 보유)
- full_stats.json   : 위 전체 기간 실행의 요약 지표
엔진: mubae/engine.py (복리, 수수료 0, 원금 $20,000)
"""
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from mubae import data
from mubae.engine import Config, run
from sweep import _init, _job, add_years, month_starts

OUT = Path(__file__).resolve().parent / "dashboard"
TICKERS = ["TQQQ", "SOXL"]
NS = [20, 30, 40]
TARGETS = [10, 15, 20, 25]
HORIZON = 3
FROM = "2011-01-01"
FULL_START = "2011-01-03"
PRINCIPAL = 20000.0
CODE = Path(__file__).read_text(encoding="utf-8")


def meta(extra):
    return [{"label": "export_dashboard.py", "value": CODE, "type": "code", "lang": "python"},
            {"label": "엔진", "value": "mubae/engine.py (무한매수법 V4.0 규칙, 복리, 수수료 0)"},
            {"label": "가격 데이터", "value": "Yahoo Finance 일봉, 액면분할 복원 실제 당시 가격, 배당 미반영"}] + extra


def dump(name, rows, extra):
    OUT.mkdir(exist_ok=True)
    with (OUT / name).open("w", encoding="utf-8") as f:
        json.dump({"rows": rows, "meta": meta(extra)}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{name}: {len(rows)}행, {(OUT / name).stat().st_size / 1024:.0f}KB")


def main():
    bars = {t: data.load(t) for t in TICKERS}

    # 1) 시작월 스윕
    jobs = []
    for t in TICKERS:
        last = bars[t][-1].date
        for st in month_starts(bars[t], FROM, None, 1):
            end = add_years(st, HORIZON)
            if end > last:
                continue
            for n in NS:
                for tg in TARGETS:
                    jobs.append(Config(ticker=t, n=n, target=tg, principal=PRINCIPAL, start=st, end=end))
    with ProcessPoolExecutor(initializer=_init, initargs=(TICKERS,)) as ex:
        stats = list(ex.map(_job, jobs, chunksize=16))
    runs = [{
        "ticker": s["ticker"], "n": int(s["n"]), "target": int(s["target"]), "start": s["start"], "end": s["end"],
        "cagr": s["cagr_pct"], "mdd": s["mdd_pct"], "total_ret": s["total_return_pct"], "cycles": s["cycles"],
        "reverse_entries": s["reverse_entries"], "blocked_exits": s["blocked_exits"],
        "worst_cycle_low": s["worst_cycle_low_pct"], "open_at_end": s["open_cycle"],
    } for s in stats]
    dump("sweep_runs.json", runs, [{"label": "조건", "value": f"시작월 {FROM}부터 매월, 각 {HORIZON}년 운용, 원금 ${PRINCIPAL:,.0f}"}])

    # 2) 전체 기간 자산 곡선 + 요약
    curves, full = [], []
    for t in TICKERS:
        b = bars[t]
        for tg in TARGETS:
            for n in NS:
                r = run(Config(ticker=t, n=n, target=tg, principal=PRINCIPAL, start=FULL_START), b)
                s = r.stats
                full.append({"ticker": t, "target": tg, "n": n, "series": f"{n}분할",
                             "cagr": s["cagr_pct"], "mdd": s["mdd_pct"], "final_equity": s["final_equity"],
                             "cycles": s["cycles"], "reverse_entries": s["reverse_entries"],
                             "start": s["start"], "end": s["end"]})
                curves += weekly(r.daily, t, tg, n)
        # 단순 보유(비교용): 첫날 종가에 원금 전부 매수, 분할은 수량 보정
        i0 = next(i for i, x in enumerate(b) if x.date >= FULL_START)
        sh, peak, mdd, daily = PRINCIPAL / b[i0].close, PRINCIPAL, 0.0, []
        for x in b[i0:]:
            if x.split != 1.0 and x is not b[i0]:
                sh *= x.split
            eq = sh * x.close
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
            daily.append({"date": x.date, "equity": eq, "drawdown": eq / peak - 1})
        yrs = (date.fromisoformat(b[-1].date) - date.fromisoformat(b[i0].date)).days / 365.25
        final = daily[-1]["equity"]
        for tg in TARGETS:
            full.append({"ticker": t, "target": tg, "n": 0, "series": "단순 보유",
                         "cagr": round(((final / PRINCIPAL) ** (1 / yrs) - 1) * 100, 2), "mdd": round(mdd * 100, 2),
                         "final_equity": round(final, 2), "cycles": None, "reverse_entries": None,
                         "start": b[i0].date, "end": b[-1].date})
        curves += weekly(daily, t, 0, 0)
    dump("equity_curves.json", curves, [{"label": "조건", "value": f"{FULL_START} 시작 ~ 데이터 끝, 주간(주 마지막 거래일), 원금 ${PRINCIPAL:,.0f}. n=0은 단순 보유"}])
    dump("full_stats.json", full, [{"label": "조건", "value": f"{FULL_START} 시작 ~ 데이터 끝, 원금 ${PRINCIPAL:,.0f}. 단순 보유는 첫날 종가에 전액 매수"}])


def weekly(daily, ticker, target, n):
    out, prev_week = [], None
    for k in range(len(daily) - 1, -1, -1):  # 뒤에서부터: 각 주의 마지막 거래일만
        d = daily[k]
        wk = date.fromisoformat(d["date"]).isocalendar()[:2]
        if wk != prev_week:
            out.append({"date": d["date"], "ticker": ticker, "target": target, "n": n,
                        "equity": round(d["equity"], 2), "drawdown": round(d["drawdown"] * 100, 2)})
            prev_week = wk
    return out[::-1]


if __name__ == "__main__":
    main()
