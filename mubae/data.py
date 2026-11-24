"""가격 데이터 로더.

Yahoo Finance 차트 API에서 일봉을 받아 '실제 당시 가격'으로 복원한 뒤 data/<티커>.csv 로 캐시한다.
- Yahoo의 OHLC는 액면분할이 소급 조정된 값이므로, 이후에 일어난 분할 비율을 곱해 당시 가격을 복원한다.
- 배당은 반영하지 않는다(실제 체결 가격 기준 시뮬레이션이므로).
- split 열: 그날 장 시작 전에 적용된 분할 비율(예: 2.0 = 2:1). 분할이 없으면 1.0.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Bar:
    date: str  # YYYY-MM-DD (미국 거래일)
    open: float
    high: float
    low: float
    close: float
    split: float = 1.0


def _fetch_yahoo(ticker: str) -> tuple[list[tuple], dict[str, float]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1=0&period2={int(time.time())}&interval=1d&events=split"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    result = payload["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        rows.append((d, o, h, l, c))
    splits = {}
    for ev in (result.get("events") or {}).get("splits", {}).values():
        d = datetime.fromtimestamp(ev["date"], tz=timezone.utc).strftime("%Y-%m-%d")
        splits[d] = ev["numerator"] / ev["denominator"]
    return rows, splits


def _to_actual(rows: list[tuple], splits: dict[str, float]) -> list[Bar]:
    bars = []
    split_dates = sorted(splits)
    for d, o, h, l, c in rows:
        factor = 1.0
        for sd in split_dates:
            if sd > d:
                factor *= splits[sd]
        bars.append(
            Bar(d, round(o * factor, 2), round(h * factor, 2), round(l * factor, 2),
                round(c * factor, 2), splits.get(d, 1.0))
        )
    # 장중(미완성) 봉 제거: 미국 동부 기준 오늘 날짜이면서 장 마감 전이면 버린다.
    now_et = datetime.now(timezone.utc) - timedelta(hours=5)
    if bars and bars[-1].date == now_et.strftime("%Y-%m-%d") and now_et.hour < 17:
        bars.pop()
    return bars


def load(ticker: str, refresh: bool = False) -> list[Bar]:
    ticker = ticker.upper()
    path = DATA_DIR / f"{ticker}.csv"
    if path.exists() and not refresh:
        with path.open(encoding="utf-8") as f:
            r = csv.DictReader(f)
            return [Bar(x["date"], float(x["open"]), float(x["high"]), float(x["low"]),
                        float(x["close"]), float(x["split"])) for x in r]
    rows, splits = _fetch_yahoo(ticker)
    bars = _to_actual(rows, splits)
    DATA_DIR.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "split"])
        for b in bars:
            w.writerow([b.date, b.open, b.high, b.low, b.close, b.split])
    return bars
