"""변수 스윕: 종목 x 분할수 x 목표수익률 x 시작일(진입 시점)을 바꿔가며 반복 실행.

예) python sweep.py                                   # TQQQ/SOXL x 20/30/40분할 x 기본 목표, 2011년부터 매월 시작, 끝까지
    python sweep.py --horizon 3                       # 각 시작일로부터 3년간만 운용
    python sweep.py --tickers TQQQ --n 20 40 --targets 10 15 20 --from 2012-01-01 --step 3
결과: results/sweep_<이름>.csv(실행별) + results/sweep_<이름>_summary.csv(조합별 요약) + 콘솔 요약
"""
import argparse
import csv
import statistics as stx
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from mubae import data
from mubae.engine import Config, run

RESULTS = Path(__file__).resolve().parent / "results"
_BARS = {}


def _init(tickers):
    for t in tickers:
        _BARS[t] = data.load(t)


def _job(cfg: Config):
    return run(cfg, _BARS[cfg.ticker], keep_daily=False).stats


def month_starts(bars, frm, to, step):
    """frm~to 사이 매 step개월 첫 거래일."""
    out, seen = [], set()
    for b in bars:
        if b.date < frm or (to and b.date > to):
            continue
        y, m = int(b.date[:4]), int(b.date[5:7])
        key = (y * 12 + m - 1)
        base = int(frm[:4]) * 12 + int(frm[5:7]) - 1
        if (key - base) % step == 0 and key not in seen:
            seen.add(key)
            out.append(b.date)
    return out


def add_years(d: str, years: float) -> str:
    y = date.fromisoformat(d)
    days = int(round(years * 365.25))
    return date.fromordinal(y.toordinal() + days).isoformat()


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser(description="무한매수법 V4.0 변수 스윕")
    ap.add_argument("--tickers", nargs="+", default=["TQQQ", "SOXL"])
    ap.add_argument("--n", nargs="+", type=float, default=[20, 30, 40])
    ap.add_argument("--targets", nargs="+", type=float, default=None,
                    help="목표수익률 목록(%%). 생략 시 종목 기본값")
    ap.add_argument("--from", dest="frm", default="2011-01-01", help="첫 시작일")
    ap.add_argument("--to", default=None, help="마지막 시작일")
    ap.add_argument("--step", type=int, default=1, help="시작일 간격(개월)")
    ap.add_argument("--horizon", type=float, default=0, help="운용 기간(년). 0이면 데이터 끝까지")
    ap.add_argument("--simple", action="store_true", help="단리")
    ap.add_argument("--fee", type=float, default=0.0)
    ap.add_argument("--principal", type=float, default=20000)
    ap.add_argument("--name", default=None)
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()

    tickers = [t.upper() for t in a.tickers]
    bars_all = {t: data.load(t) for t in tickers}
    jobs = []
    for t in tickers:
        bars = bars_all[t]
        last = bars[-1].date
        for st in month_starts(bars, a.frm, a.to, a.step):
            end = add_years(st, a.horizon) if a.horizon > 0 else None
            if end and end > last:
                continue  # 운용 기간을 다 채우지 못하는 시작일은 제외
            for n in a.n:
                for tg in (a.targets or [None]):
                    jobs.append(Config(ticker=t, n=n, target=tg, principal=a.principal, start=st, end=end,
                                       compound=not a.simple, fee_rate=a.fee))
    print(f"실행 {len(jobs)}건 ...")
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init, initargs=(tickers,)) as ex:
        rows = list(ex.map(_job, jobs, chunksize=16))

    RESULTS.mkdir(exist_ok=True)
    name = a.name or f"{'-'.join(tickers)}_h{a.horizon:g}{'_simple' if a.simple else ''}"
    with (RESULTS / f"sweep_{name}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    groups = {}
    for r in rows:
        groups.setdefault((r["ticker"], r["n"], r["target"]), []).append(r)
    summary = []
    for (t, n, tg), rs in sorted(groups.items()):
        cagr = [r["cagr_pct"] for r in rs]
        mdd = [r["mdd_pct"] for r in rs]
        tot = [r["total_return_pct"] for r in rs]
        summary.append({
            "ticker": t, "n": n, "target": tg, "runs": len(rs),
            "cagr_median": round(stx.median(cagr), 2), "cagr_p10": round(pct(cagr, 0.1), 2),
            "cagr_p90": round(pct(cagr, 0.9), 2), "cagr_min": min(cagr), "cagr_max": max(cagr),
            "total_ret_median": round(stx.median(tot), 2),
            "loss_runs_pct": round(sum(1 for x in tot if x < 0) / len(rs) * 100, 1),
            "mdd_median": round(stx.median(mdd), 2), "mdd_p10(나쁜쪽)": round(pct(mdd, 0.1), 2),
            "mdd_worst": min(mdd),
            "reverse_entries_avg": round(stx.mean(r["reverse_entries"] for r in rs), 2),
            "blocked_exits_total": sum(r["blocked_exits"] for r in rs),
            "avg_cycle_days_median": stx.median([r["avg_cycle_days"] for r in rs if r["avg_cycle_days"]] or [0]),
        })
    with (RESULTS / f"sweep_{name}_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    hz = f"{a.horizon:g}년 운용" if a.horizon > 0 else "데이터 끝까지 운용"
    print(f"\n=== 조합별 요약 ({hz}, 시작일 {a.frm}부터 {a.step}개월 간격, {'단리' if a.simple else '복리'}) ===")
    print(f"{'종목':<5}{'분할':>5}{'목표':>6}{'실행':>6} | {'CAGR중앙':>8}{'CAGR10%':>9}{'CAGR90%':>9} | "
          f"{'MDD중앙':>8}{'MDD10%':>8}{'MDD최악':>8} | {'손실비율':>7}{'리버스':>7}")
    for s in summary:
        print(f"{s['ticker']:<5}{s['n']:>5g}{s['target']:>6g}{s['runs']:>6} | {s['cagr_median']:>8}{s['cagr_p10']:>9}"
              f"{s['cagr_p90']:>9} | {s['mdd_median']:>8}{s['mdd_p10(나쁜쪽)']:>8}{s['mdd_worst']:>8} | "
              f"{s['loss_runs_pct']:>6}%{s['reverse_entries_avg']:>7}")
    print(f"\n저장: results/sweep_{name}.csv, results/sweep_{name}_summary.csv")


if __name__ == "__main__":
    main()
