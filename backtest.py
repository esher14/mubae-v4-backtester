"""단일 백테스트 실행.

예) python backtest.py --ticker TQQQ --n 20 --start 2015-01-01 --end 2020-12-31
    python backtest.py --ticker SOXL --n 40 --target 20 --simple --fee 0.0007
결과: 콘솔 요약 + results/<이름>_daily.csv(일별 기록) + results/<이름>_cycles.csv(사이클별 기록)
"""
import argparse
import csv
from pathlib import Path

from mubae import data
from mubae.engine import Config, run

RESULTS = Path(__file__).resolve().parent / "results"


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    keys = list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="무한매수법 V4.0 백테스트")
    ap.add_argument("--ticker", default="TQQQ")
    ap.add_argument("--n", type=float, default=20, help="분할수")
    ap.add_argument("--target", type=float, default=None, help="목표수익률(%%). 기본 TQQQ 15 / SOXL 20")
    ap.add_argument("--principal", type=float, default=20000)
    ap.add_argument("--start", default=None, help="시작일 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="종료일 YYYY-MM-DD")
    ap.add_argument("--simple", action="store_true", help="단리(사이클 종료 시 원금 초과분 인출)")
    ap.add_argument("--fee", type=float, default=0.0, help="편도 수수료율 (예: 0.0007)")
    ap.add_argument("--refresh", action="store_true", help="가격 데이터 새로 받기")
    ap.add_argument("--name", default=None, help="결과 파일 이름")
    a = ap.parse_args()

    bars = data.load(a.ticker, refresh=a.refresh)
    cfg = Config(ticker=a.ticker, n=a.n, target=a.target, principal=a.principal, start=a.start,
                 end=a.end, compound=not a.simple, fee_rate=a.fee)
    r = run(cfg, bars)
    s = r.stats

    print(f"\n=== {s['ticker']} {s['n']:g}분할 목표 {s['target']:g}% ({'복리' if s['compound'] else '단리'}) "
          f"{s['start']} ~ {s['end']} ({s['years']}년) ===")
    print(f"최종 평가금 ${s['final_equity']:,.2f}  총수익률 {s['total_return_pct']}%  CAGR {s['cagr_pct']}%")
    print(f"MDD {s['mdd_pct']}%  ({s['mdd_peak']} 고점 → {s['mdd_trough']} 저점)")
    print(f"완료 사이클 {s['cycles']}회 (수익 {s['win_cycles']}회)  평균 {s['avg_cycle_days']}거래일, 최장 {s['max_cycle_days']}거래일")
    print(f"사이클 평균수익률 {s['avg_cycle_ret_pct']}%  최악 사이클 {s['worst_cycle_ret_pct']}%  "
          f"사이클 중 최저 평가손익 {s['worst_cycle_low_pct']}%")
    print(f"리버스 진입 {s['reverse_entries']}회, 리버스 일수 {s['reverse_days']}일, 리버스 중 소진 MOC {s['exhaust_moc']}회, "
          f"종료 보류(T>n-1) {s['blocked_exits']}회, 최대 T {s['max_T']}")
    if s["open_cycle"]:
        print(f"진행 중 사이클: {s['open_mode']} T={s['open_T']} {s['open_days']}거래일째, 평가손익 {s['open_unrealized_pct']}%")
    if r.next_orders:
        print("\n[다음 거래일 주문표]")
        for line in r.next_orders.lines():
            print("  " + line)

    RESULTS.mkdir(exist_ok=True)
    name = a.name or f"{s['ticker']}_{s['n']:g}_{s['target']:g}_{s['start']}_{s['end']}"
    write_csv(RESULTS / f"{name}_daily.csv", r.daily)
    write_csv(RESULTS / f"{name}_cycles.csv", r.cycles)
    print(f"\n저장: results/{name}_daily.csv, results/{name}_cycles.csv")


if __name__ == "__main__":
    main()
