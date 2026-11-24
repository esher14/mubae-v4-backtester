/* 무한매수법 V4.0 백테스트 엔진 (브라우저용). mubae/engine.py 를 그대로 옮긴 것.
 * 확정된 해석은 engine.py 머리말과 같다. 결과가 Python 엔진과 같은지 test_js_engine.py 로 검증한다. */
(function (root) {
  'use strict';
  const DEFAULT_TARGET = { TQQQ: 15, SOXL: 20 };
  const NORMAL = 'NORMAL', REVERSE = 'REVERSE';

  function parseCSV(text) {
    const lines = text.trim().split(/\r?\n/);
    const head = lines[0].split(',');
    const ix = (k) => head.indexOf(k);
    const iD = ix('date'), iO = ix('open'), iH = ix('high'), iL = ix('low'), iC = ix('close'), iS = ix('split');
    const out = [];
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].split(',');
      out.push({ date: c[iD], open: +c[iO], high: +c[iH], low: +c[iL], close: +c[iC], split: +c[iS] || 1 });
    }
    return out;
  }

  function targetPct(cfg) {
    if (cfg.target != null && cfg.target !== '' && !isNaN(cfg.target)) return +cfg.target;
    return DEFAULT_TARGET[String(cfg.ticker).toUpperCase()] ?? 15;
  }
  function starPct(cfg, T) { const t = targetPct(cfg); return t - t / (cfg.n / 2) * T; }
  function rnd(x, tick) { return tick > 0 ? Math.round(x * 100) / 100 : Math.round(x * 10000) / 10000; }

  function makeOrders(st, cfg, tick, sma5, capital) {
    const n = cfg.n, half = n / 2, tgt = targetPct(cfg);
    if (st.shares === 0) {
      const amt = capital / n;
      return { kind: 'FIRST', amt, buys: [], tierAmt: amt, tierBase: 0, locSells: [], mocSell: 0, limitSell: null };
    }
    if (st.mode === NORMAL) {
      const P = starPct(cfg, st.T);
      const star = rnd(st.avg * (1 + P / 100), tick);
      const amt = st.cash / Math.max(n - st.T, 1e-9);
      const q4 = Math.floor(st.shares / 4);
      const o = { kind: st.T < half ? 'FRONT' : 'BACK', amt, starPct: P, star, buys: [], tierAmt: amt, tierBase: 0,
        locSells: [[star, q4, '쿼터매도']], mocSell: 0, limitSell: [rnd(st.avg * (1 + tgt / 100), tick), st.shares - q4] };
      const pA = Math.max(star - tick, 0.0001);
      if (st.T < half) {
        const qA = Math.floor(amt * 0.5 / pA);
        const pB = rnd(st.avg, tick);
        const qB = Math.max(0, Math.floor(amt / pB) - qA);
        o.buys = [[pA, qA, 0.5, '별지점'], [pB, qB, 0.5, '평단']];
        o.tierBase = qA + qB;
      } else {
        const qA = Math.floor(amt / pA);
        o.buys = [[pA, qA, 1.0, '별지점']];
        o.tierBase = qA;
      }
      return o;
    }
    const qSell = Math.floor(st.shares / half);
    if (st.revFirst) return { kind: 'REV_FIRST', amt: 0, buys: [], tierAmt: 0, tierBase: 0, locSells: [], mocSell: qSell, limitSell: null };
    const star = rnd(sma5, tick);
    const amt = st.cash / 4;
    const pB = Math.max(star - tick, 0.0001);
    const qB = Math.floor(amt / pB);
    if (qB <= 0) return { kind: 'REV_EXHAUST', amt, star, buys: [], tierAmt: 0, tierBase: 0, locSells: [], mocSell: qSell, limitSell: null };
    return { kind: 'REV', amt, star, buys: [[pB, qB, null, '별지점']], tierAmt: amt, tierBase: qB,
      locSells: [[star, qSell, '별지점 매도']], mocSell: 0, limitSell: null };
  }

  function orderLines(o, nTiers = 5) {
    const out = [];
    for (const [p, q, , lab] of o.buys) if (q > 0) out.push(`[LOC 매수] $${p.toFixed(2)} × ${q}주 (${lab})`);
    if (o.tierAmt > 0) {
      for (let k = 1; k <= nTiers; k++) out.push(`[LOC 매수] $${(o.tierAmt / (o.tierBase + k)).toFixed(2)} × 1주 (추가)`);
      out.push('… 추가 1주 주문 계속');
    }
    for (const [p, q, lab] of o.locSells) if (q > 0) out.push(`[LOC 매도] $${p.toFixed(2)} × ${q}주 (${lab})`);
    if (o.mocSell > 0) out.push(`[MOC 매도] ${o.mocSell}주`);
    if (o.limitSell && o.limitSell[1] > 0) out.push(`[지정가 매도] $${o.limitSell[0].toFixed(2)} × ${o.limitSell[1]}주`);
    return out;
  }

  function run(cfgIn, bars) {
    const cfg = Object.assign({ ticker: 'TQQQ', n: 20, target: null, principal: 20000, start: null, end: null,
      compound: true, feeRate: 0, tick: 0.01, tickExemptBelow: 2.0 }, cfgIn);
    const n = cfg.n, tgt = targetPct(cfg), fee = cfg.feeRate;
    let iStart = -1, iEnd = -1;
    for (let i = 0; i < bars.length; i++) {
      const d = bars[i].date;
      if ((cfg.start == null || d >= cfg.start) && (cfg.end == null || d <= cfg.end)) { if (iStart < 0) iStart = i; iEnd = i; }
    }
    if (iStart < 0) throw new Error('기간 내 데이터가 없습니다');

    const st = { cash: cfg.principal, shares: 0, avg: 0, T: 0, mode: NORMAL, revFirst: false };
    let withdrawn = 0;
    let closes = [];
    const daily = [], cycles = [];
    let cyc = null;
    let peakEq = cfg.principal, mdd = 0, mddPeak = null, mddTrough = null, curPeak = null;
    const cnt = { reverseEntries: 0, blockedExits: 0, exhaustMoc: 0, reverseDays: 0, tickExemptDays: 0 };
    let maxT = 0;

    function buy(q, price) {
      const cost = q * price;
      st.avg = (st.avg * st.shares + cost) / (st.shares + q);
      st.shares += q;
      st.cash -= cost * (1 + fee);
    }
    function sell(q, price) {
      q = Math.min(q, st.shares);
      const gross = q * price;
      st.cash += gross * (1 - fee);
      const pnl = q * (price - st.avg) - gross * fee;
      st.shares -= q;
      if (st.shares === 0) st.avg = 0;
      return pnl;
    }

    for (let i = Math.max(0, iStart - 5); i <= iEnd; i++) {
      const b = bars[i];
      if (b.split !== 1) {
        const r = b.split;
        closes = closes.map((c) => c / r);
        if (st.shares) { st.shares = Math.floor(st.shares * r + 1e-9); st.avg /= r; }
      }
      if (i < iStart) { closes.push(b.close); if (closes.length > 5) closes.shift(); continue; }

      const prevClose = closes.length ? closes[closes.length - 1] : b.open;
      const tick = prevClose >= cfg.tickExemptBelow ? cfg.tick : 0;
      if (tick === 0) cnt.tickExemptDays++;
      const sma5 = closes.length ? closes.reduce((a, c) => a + c, 0) / closes.length : prevClose;
      const capital = st.cash;
      const o = makeOrders(st, cfg, tick, sma5, capital);
      const modeBefore = st.mode, Tbefore = st.T;
      const C = b.close;
      const events = [];
      let realized = 0, soldLimit = false, soldLoc = false, soldMoc = false;

      if (o.limitSell && o.limitSell[1] > 0 && b.high >= o.limitSell[0]) {
        const [p, q] = o.limitSell;
        realized += sell(q, p); soldLimit = true; events.push(`지정가매도 ${q}주 @${p.toFixed(2)}`);
      }
      if (o.mocSell > 0 && st.shares > 0) {
        const q = Math.min(o.mocSell, st.shares);
        realized += sell(q, C); soldMoc = true; events.push(`MOC매도 ${q}주 @${C.toFixed(2)}`);
      }
      for (const [p, q0, lab] of o.locSells) {
        if (q0 > 0 && st.shares > 0 && C >= p) {
          const q = Math.min(q0, st.shares);
          realized += sell(q, C); soldLoc = true; events.push(`LOC매도(${lab}) ${q}주 @${C.toFixed(2)}`);
        }
      }
      let bought = 0, tInc = 0;
      if (!soldLoc && o.kind !== 'REV_FIRST' && o.kind !== 'REV_EXHAUST') {
        for (const [p, q, inc] of o.buys) if (q > 0 && C <= p) { bought += q; tInc += inc || 0; }
        if (o.tierAmt > 0 && C <= o.tierAmt / (o.tierBase + 1)) bought += Math.floor(o.tierAmt / C) - o.tierBase;
        const affordable = C > 0 ? Math.floor(st.cash / (C * (1 + fee))) : 0;
        bought = Math.max(0, Math.min(bought, affordable));
        if (bought > 0) { buy(bought, C); events.push(`LOC매수 ${bought}주 @${C.toFixed(2)}`); }
      }

      if (o.kind === 'FIRST') {
        if (bought > 0) {
          st.T = 1; st.mode = NORMAL;
          cyc = { start: b.date, startCapital: capital, maxT: 1, reverseEntries: 0, days: 0, peak: capital, mdd: 0, lowRet: 0 };
          events.push('사이클 시작');
        }
      } else if (o.kind === 'FRONT' || o.kind === 'BACK') {
        if (bought > 0 && tInc === 0) tInc = 1;
        if (soldLimit) st.T = st.T * 0.25 + tInc;
        else if (soldLoc) st.T = st.T * 0.75;
        else st.T += tInc;
      } else {
        if (soldMoc || soldLoc) st.T *= (1 - 2 / n);
        if (bought > 0) st.T += (n - st.T) * 0.25;
        if (o.kind === 'REV_EXHAUST') cnt.exhaustMoc++;
        if (o.kind === 'REV_FIRST') st.revFirst = false;
      }

      const cycleEnd = st.shares === 0 && o.kind !== 'FIRST' && (soldLimit || soldLoc || soldMoc);
      let endedCycle = null;
      if (cycleEnd) {
        cyc.days += 1;
        Object.assign(cyc, { end: b.date, endCash: st.cash, profit: st.cash - cyc.startCapital,
          retPct: (st.cash / cyc.startCapital - 1) * 100, finalT: Tbefore });
        cycles.push(cyc); endedCycle = cyc; cyc = null;
        st.T = 0; st.mode = NORMAL; st.revFirst = false;
        if (!cfg.compound && st.cash > cfg.principal) { withdrawn += st.cash - cfg.principal; st.cash = cfg.principal; }
        events.push('사이클 종료');
      } else if (st.shares > 0) {
        if (st.mode === NORMAL && st.T > n - 1) {
          st.mode = REVERSE; st.revFirst = true; cnt.reverseEntries++; cyc.reverseEntries++; events.push('리버스 진입');
        } else if (st.mode === REVERSE && C > st.avg * (1 - tgt / 100)) {
          if (st.T > n - 1) { cnt.blockedExits++; events.push('리버스 종료조건 충족, T>n-1로 유지'); }
          else { st.mode = NORMAL; events.push('리버스 종료 → 일반모드'); }
        }
      }

      if (modeBefore === REVERSE) cnt.reverseDays++;
      maxT = Math.max(maxT, st.T);
      closes.push(C); if (closes.length > 5) closes.shift();

      const equity = st.cash + st.shares * C + withdrawn;
      if (equity > peakEq) { peakEq = equity; curPeak = b.date; }
      const dd = equity / peakEq - 1;
      if (dd < mdd) { mdd = dd; mddPeak = curPeak; mddTrough = b.date; }
      if (cyc) {
        cyc.days += 1;
        cyc.maxT = Math.max(cyc.maxT, st.T);
        const ceq = st.cash + st.shares * C;
        cyc.peak = Math.max(cyc.peak, ceq);
        cyc.mdd = Math.min(cyc.mdd, ceq / cyc.peak - 1);
        cyc.lowRet = Math.min(cyc.lowRet, ceq / cyc.startCapital - 1);
      }
      daily.push({ date: b.date, close: C, kind: o.kind, mode: st.mode, modeBefore, T: st.T, Tbefore, starPct: o.starPct ?? null,
        star: o.star ?? null, amt: o.amt, shares: st.shares, avg: st.avg, cash: st.cash, withdrawn, equity, dd, realized,
        events, cycleEnd: endedCycle });
    }

    const last = bars[iEnd], firstDate = bars[iStart].date;
    const years = Math.max((Date.parse(last.date) - Date.parse(firstDate)) / 86400000 / 365.25, 1e-9);
    const finalEq = st.cash + st.shares * last.close + withdrawn;
    const stats = {
      ticker: String(cfg.ticker).toUpperCase(), n, target: tgt, compound: cfg.compound, start: firstDate, end: last.date, years,
      principal: cfg.principal, finalEquity: finalEq, totalReturnPct: (finalEq / cfg.principal - 1) * 100,
      cagrPct: finalEq > 0 ? (Math.pow(finalEq / cfg.principal, 1 / years) - 1) * 100 : -100,
      mddPct: mdd * 100, mddPeak, mddTrough, cycles: cycles.length, winCycles: cycles.filter((c) => c.profit > 0).length,
      maxT, ...cnt,
      openCycle: cyc, openMode: cyc ? st.mode : null, openT: cyc ? st.T : null,
      openUnrealizedPct: cyc ? ((st.cash + st.shares * last.close) / cyc.startCapital - 1) * 100 : null,
    };
    const tickN = last.close >= cfg.tickExemptBelow ? cfg.tick : 0;
    const sma5 = closes.reduce((a, c) => a + c, 0) / closes.length;
    const nextOrders = makeOrders(st, cfg, tickN, sma5, st.cash);
    return { cfg, stats, cycles, daily, nextOrders, state: Object.assign({}, st) };
  }

  root.MubaeEngine = { DEFAULT_TARGET, parseCSV, targetPct, starPct, makeOrders, orderLines, run };
})(typeof window !== 'undefined' ? window : globalThis);
