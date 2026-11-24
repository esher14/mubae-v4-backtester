"""브라우저용 엔진(app/engine.js)이 Python 엔진(mubae/engine.py)과 같은 결과를 내는지 검증.
Microsoft Edge 헤드리스로 JS를 실행한다.  실행: python test_js_engine.py
"""
import html
import json
import re
import subprocess
import tempfile
from pathlib import Path

from mubae import data
from mubae.engine import Config, run

ROOT = Path(__file__).resolve().parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CASES = [
    dict(ticker="TQQQ", n=20, target=None, start="2011-01-03", compound=True, fee=0.0),
    dict(ticker="TQQQ", n=40, target=10, start="2015-06-01", compound=True, fee=0.0),
    dict(ticker="TQQQ", n=30, target=25, start="2019-01-02", end="2023-06-30", compound=False, fee=0.0007),
    dict(ticker="SOXL", n=20, target=None, start="2011-01-03", compound=True, fee=0.0),
    dict(ticker="SOXL", n=40, target=15, start="2020-02-03", compound=False, fee=0.0),
    dict(ticker="SOXL", n=30, target=20, start="2014-03-03", end="2021-12-31", compound=True, fee=0.001),
]


def py_result(c):
    cfg = Config(ticker=c["ticker"], n=c["n"], target=c["target"], principal=20000, start=c["start"],
                 end=c.get("end"), compound=c["compound"], fee_rate=c["fee"])
    r = run(cfg, data.load(c["ticker"]))
    return {"final": round(r.stats["final_equity"], 2), "mdd": round(r.stats["mdd_pct"], 2),
            "cycles": r.stats["cycles"], "rev": r.stats["reverse_entries"], "blocked": r.stats["blocked_exits"],
            "ends": [cy["end"] for cy in r.cycles], "lastT": round(r.daily[-1]["T"], 6)}


def main():
    csv = {t: (ROOT / "data" / f"{t}.csv").read_text(encoding="utf-8") for t in ("TQQQ", "SOXL")}
    engine = (ROOT / "app" / "engine.js").read_text(encoding="utf-8")
    js_cases = [{"ticker": c["ticker"], "n": c["n"], "target": c["target"], "principal": 20000, "start": c["start"],
                 "end": c.get("end"), "compound": c["compound"], "feeRate": c["fee"]} for c in CASES]
    page = f"""<!doctype html><meta charset="utf-8"><pre id="out">pending</pre>
<script>{engine}</script>
<script>
const CSV = {json.dumps(csv)};
const CASES = {json.dumps(js_cases)};
const bars = {{TQQQ: MubaeEngine.parseCSV(CSV.TQQQ), SOXL: MubaeEngine.parseCSV(CSV.SOXL)}};
try {{
  const res = CASES.map(c => {{ const r = MubaeEngine.run(c, bars[c.ticker]); return {{
    final: Math.round(r.stats.finalEquity*100)/100, mdd: Math.round(r.stats.mddPct*100)/100, cycles: r.stats.cycles,
    rev: r.stats.reverseEntries, blocked: r.stats.blockedExits, ends: r.cycles.map(x => x.end),
    lastT: Math.round(r.daily[r.daily.length-1].T*1e6)/1e6 }}; }});
  document.getElementById('out').textContent = JSON.stringify(res);
}} catch (e) {{ document.getElementById('out').textContent = 'ERROR ' + e.stack; }}
</script>"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.html"
        p.write_text(page, encoding="utf-8")
        out = subprocess.run([EDGE, "--headless", "--disable-gpu", f"--user-data-dir={td}\\prof", "--dump-dom",
                              p.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=120).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', out, re.S)
    txt = html.unescape(m.group(1)) if m else out[:500]
    if not txt.startswith("["):
        print("JS 실행 실패:", txt)
        raise SystemExit(1)
    js = json.loads(txt)
    fails = 0
    for c, j in zip(CASES, js):
        p = py_result(c)
        diff = {k: (p[k], j[k]) for k in p if (abs(p[k] - j[k]) > 0.011 if isinstance(p[k], float) else p[k] != j[k])}
        label = f"{c['ticker']} {c['n']}분할 목표{c['target']} {c['start']}~{c.get('end') or '끝'}"
        if diff:
            fails += 1
            print("FAIL", label, {k: (v if k != "ends" else f"py {len(v[0])}개 / js {len(v[1])}개") for k, v in diff.items()})
        else:
            print("OK  ", label, f"최종 ${p['final']:,.2f}, MDD {p['mdd']}%, 사이클 {p['cycles']}회")
    print("\n전체 결과:", "모두 일치" if not fails else f"{fails}건 불일치")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
