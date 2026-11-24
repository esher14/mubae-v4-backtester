"""웹 백테스터(app/)용 가격 데이터 갱신. GitHub Actions가 매일 실행한다.

Yahoo Finance 일봉을 받아 액면분할을 복원한 실제 당시 가격으로 app/data/<티커>.csv 를 다시 쓰고,
app/data/meta.json 에 갱신 시각과 종목별 마지막 거래일을 남긴다.
받기에 실패한 종목은 기존 파일을 그대로 둔다(저장소에 커밋된 파일이 비상용 사본).
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mubae import data  # noqa: E402

TICKERS = ["TQQQ", "SOXL", "QQQ", "SOXX"]  # QQQ·SOXX는 기초지수 단순 보유 비교용
OUT = ROOT / "app" / "data"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.setdefault("last_date", {})
    failed = []
    for t in TICKERS:
        try:
            bars = data.load(t, refresh=True)
            if len(bars) < 1000:
                raise ValueError(f"데이터가 너무 적습니다({len(bars)}행)")
            shutil.copyfile(data.DATA_DIR / f"{t}.csv", OUT / f"{t}.csv")
            meta["last_date"][t] = bars[-1].date
            print(f"{t}: {len(bars)}행, {bars[0].date} ~ {bars[-1].date}")
        except Exception as e:  # 실패해도 기존 파일로 배포는 계속한다
            failed.append(t)
            print(f"::warning::{t} 갱신 실패, 기존 파일 유지: {e}")
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    meta["updated_at"] = kst.strftime("%Y-%m-%d %H:%M KST")
    meta["failed"] = failed
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
