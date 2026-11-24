/* 무매 V4 기록장 계산부. 실제 체결 기록을 날짜순으로 다시 계산해 T·평단·잔금·모드와 다음 주문표를 만든다.
 * 주문 계산(makeOrders)은 백테스터 엔진(engine.js)을 그대로 쓰고, 체결 후 T 변화 규칙도 엔진과 같다.
 * 엔진은 가격으로 체결을 가정하지만, 여기서는 사용자가 기록한 체결을 그대로 반영한다. */
(function (root) {
  'use strict';
  const E = root.MubaeEngine;

  // 체결 역할: 어떤 주문이 체결됐는지에 따라 T가 달라지므로 역할로 기록한다
  const ROLES = {
    first: { side: 'buy', type: 'LOC', label: '첫 매수', strat: true },
    starBuy: { side: 'buy', type: 'LOC', label: '별지점 매수', strat: true },
    avgBuy: { side: 'buy', type: 'LOC', label: '평단 매수', strat: true },
    tierBuy: { side: 'buy', type: 'LOC', label: '추가 1주 매수', strat: true },
    revBuy: { side: 'buy', type: 'LOC', label: '쿼터매수', strat: true },
    quarterSell: { side: 'sell', type: 'LOC', label: '쿼터매도', strat: true },
    revSell: { side: 'sell', type: 'LOC', label: '별지점 매도', strat: true },
    limitSell: { side: 'sell', type: '지정가', label: '목표가 매도', strat: true },
    mocSell: { side: 'sell', type: 'MOC', label: 'MOC 매도', strat: true },
    manualBuy: { side: 'buy', type: '수동', label: '기타 매수', strat: false },
    manualSell: { side: 'sell', type: '수동', label: '기타 매도', strat: false },
  };
  const SELL_ORDER = ['limitSell', 'mocSell', 'quarterSell', 'revSell', 'manualSell'];

  function cfgOf(tr) {
    return { ticker: tr.ticker, n: +tr.n, target: tr.target === '' || tr.target == null ? null : +tr.target, feeRate: (+tr.feeRate || 0) / 100 };
  }

  // 그날 주문(o)에 대해 기록 입력 칸 목록을 만든다
  function orderRows(o) {
    const rows = [];
    if (o.kind === 'FIRST') {
      rows.push({ role: 'first', plan: `LOC 매수: 1회 매수금 $${o.amt.toFixed(2)}어치(큰수 LOC + 추가 1주 포함)` });
      return rows;
    }
    if (o.kind === 'FRONT' || o.kind === 'BACK') {
      const [a, b] = o.buys;
      if (a) rows.push({ role: 'starBuy', plan: `LOC 매수 $${a[0].toFixed(2)} × ${a[1]}주`, planQty: a[1], limit: a[0] });
      if (b) rows.push({ role: 'avgBuy', plan: `LOC 매수 $${b[0].toFixed(2)} × ${b[1]}주`, planQty: b[1], limit: b[0] });
      rows.push({ role: 'tierBuy', plan: `추가 1주 LOC 매수 (첫 가격 $${(o.tierAmt / (o.tierBase + 1)).toFixed(2)}부터)`, tier: true });
      const [ls] = o.locSells;
      if (ls && ls[1] > 0) rows.push({ role: 'quarterSell', plan: `LOC 매도 $${ls[0].toFixed(2)} × ${ls[1]}주`, planQty: ls[1], limit: ls[0] });
      if (o.limitSell && o.limitSell[1] > 0) rows.push({ role: 'limitSell', plan: `지정가 매도 $${o.limitSell[0].toFixed(2)} × ${o.limitSell[1]}주`, planQty: o.limitSell[1], limit: o.limitSell[0], fixedPrice: true });
      return rows;
    }
    if (o.kind === 'REV_FIRST' || o.kind === 'REV_EXHAUST') {
      rows.push({ role: 'mocSell', plan: `MOC 매도 ${o.mocSell}주${o.kind === 'REV_EXHAUST' ? ' (잔금 소진)' : ' (리버스 1일차)'}`, planQty: o.mocSell });
      return rows;
    }
    const [bb] = o.buys;
    if (bb) rows.push({ role: 'revBuy', plan: `LOC 매수 $${bb[0].toFixed(2)} × ${bb[1]}주`, planQty: bb[1], limit: bb[0] });
    rows.push({ role: 'tierBuy', plan: `추가 1주 LOC 매수 (첫 가격 $${(o.tierAmt / (o.tierBase + 1)).toFixed(2)}부터)`, tier: true });
    const [rs] = o.locSells;
    if (rs && rs[1] > 0) rows.push({ role: 'revSell', plan: `LOC 매도 $${rs[0].toFixed(2)} × ${rs[1]}주`, planQty: rs[1], limit: rs[0] });
    return rows;
  }

  // 종가(와 고가)로 규칙상 체결됐을 내역을 추정한다. 입력 칸 기본값용. 백테스트 엔진의 체결 가정과 같다.
  function autoJudge(o, st, close, high, feeRate) {
    const C = +close, fills = [];
    if (!(C > 0)) return fills;
    let shares = st.shares, soldLoc = false;
    if (o.limitSell && o.limitSell[1] > 0 && high != null && high >= o.limitSell[0]) {
      const q = Math.min(o.limitSell[1], shares);
      fills.push({ role: 'limitSell', qty: q, price: o.limitSell[0] }); shares -= q;
    }
    if (o.mocSell > 0 && shares > 0) {
      const q = Math.min(o.mocSell, shares);
      fills.push({ role: 'mocSell', qty: q, price: C }); shares -= q;
    }
    for (const [p, q0] of o.locSells) {
      if (q0 > 0 && shares > 0 && C >= p) {
        const q = Math.min(q0, shares);
        fills.push({ role: o.kind === 'REV' ? 'revSell' : 'quarterSell', qty: q, price: C }); shares -= q; soldLoc = true;
      }
    }
    if (soldLoc || o.kind === 'REV_FIRST' || o.kind === 'REV_EXHAUST') return fills;
    const buys = [];
    if (o.kind === 'FIRST') {
      const q = Math.floor(o.amt / C);
      if (q > 0) buys.push({ role: 'first', qty: q, price: C });
    } else {
      o.buys.forEach(([p, q], k) => {
        if (q > 0 && C <= p) buys.push({ role: o.kind === 'REV' ? 'revBuy' : k === 0 ? 'starBuy' : 'avgBuy', qty: q, price: C });
      });
      if (o.tierAmt > 0 && C <= o.tierAmt / (o.tierBase + 1)) {
        const q = Math.floor(o.tierAmt / C) - o.tierBase;
        if (q > 0) buys.push({ role: 'tierBuy', qty: q, price: C });
      }
    }
    // 잔금보다 많이 살 수는 없다(뒤쪽 주문부터 줄인다)
    let cash = st.cash;
    for (const f of fills) cash += f.qty * f.price * (1 - feeRate);
    let room = Math.floor(cash / (C * (1 + feeRate)));
    const out = [];
    for (const b of buys) { const q = Math.max(0, Math.min(b.qty, room)); room -= q; if (q > 0) out.push(Object.assign({}, b, { qty: q })); }
    return fills.concat(out);
  }

  // 하루치 체결을 상태(st)에 반영한다. T 변화 규칙은 engine.js run()과 같다.
  function applyDay(st, o, fills, close, cfg) {
    const n = cfg.n, fee = cfg.feeRate, tgt = E.targetPct(cfg);
    const events = [];
    let realized = 0, soldLimit = false, soldLoc = false, soldMoc = false, soldAny = false, stratBought = 0, tInc = 0;
    const sells = fills.filter((f) => ROLES[f.role] && ROLES[f.role].side === 'sell' && f.qty > 0)
      .sort((a, b) => SELL_ORDER.indexOf(a.role) - SELL_ORDER.indexOf(b.role));
    const outFills = [];
    for (const f of sells) {
      const q = Math.min(+f.qty, st.shares);
      if (q <= 0) continue;
      const gross = q * f.price;
      st.cash += gross * (1 - fee);
      const pnl = q * (f.price - st.avg) - gross * fee;
      st.shares -= q;
      if (st.shares === 0) st.avg = 0;
      realized += pnl; soldAny = true;
      if (f.role === 'limitSell') soldLimit = true;
      else if (f.role === 'quarterSell' || f.role === 'revSell') soldLoc = true;
      else if (f.role === 'mocSell') soldMoc = true;
      outFills.push({ role: f.role, qty: q, price: +f.price, pnl });
    }
    for (const f of fills) {
      const R = ROLES[f.role];
      if (!R || R.side !== 'buy' || !(f.qty > 0)) continue;
      const q = +f.qty, cost = q * f.price;
      st.avg = (st.avg * st.shares + cost) / (st.shares + q);
      st.shares += q;
      st.cash -= cost * (1 + fee);
      if (R.strat) stratBought += q;
      if (f.role === 'starBuy') tInc += o.kind === 'FRONT' ? 0.5 : 1;
      if (f.role === 'avgBuy') tInc += 0.5;
      outFills.push({ role: f.role, qty: q, price: +f.price, pnl: 0 });
    }
    let cycleStart = false;
    if (o.kind === 'FIRST') {
      if (stratBought > 0) { st.T = 1; st.mode = 'NORMAL'; cycleStart = true; events.push('사이클 시작'); }
    } else if (o.kind === 'FRONT' || o.kind === 'BACK') {
      if (stratBought > 0 && tInc === 0) tInc = 1;
      if (soldLimit) st.T = st.T * 0.25 + tInc;
      else if (soldLoc) st.T = st.T * 0.75 + tInc;
      else st.T += tInc;
    } else {
      if (soldMoc || soldLoc) st.T *= (1 - 2 / n);
      if (stratBought > 0) st.T += (n - st.T) * 0.25;
      if (o.kind === 'REV_FIRST') st.revFirst = false;
    }
    let cycleEnd = false;
    if (st.shares === 0 && o.kind !== 'FIRST' && soldAny) {
      cycleEnd = true;
      st.T = 0; st.mode = 'NORMAL'; st.revFirst = false;
      events.push('사이클 종료');
    } else if (st.shares > 0 && close > 0) {
      if (st.mode === 'NORMAL' && st.T > n - 1) { st.mode = 'REVERSE'; st.revFirst = true; events.push('리버스 진입'); }
      else if (st.mode === 'REVERSE' && close > st.avg * (1 - tgt / 100)) {
        if (st.T > n - 1) events.push('리버스 종료조건 충족, T>n-1로 유지');
        else { st.mode = 'NORMAL'; events.push('리버스 종료 → 일반모드'); }
      }
    }
    return { realized, events, fills: outFills, cycleStart, cycleEnd };
  }

  function addDays(d, k) { const t = new Date(d + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() + k); return t.toISOString().slice(0, 10); }
  function nextWeekday(d) { let x = addDays(d, 1); while ([0, 6].includes(new Date(x + 'T00:00:00Z').getUTCDay())) x = addDays(x, 1); return x; }

  // 기록 전체를 처음부터 다시 계산한다
  function replay(tr, bars) {
    const cfg = cfgOf(tr), n = cfg.n;
    const s0 = tr.start;
    const st = { cash: +s0.cash, shares: +s0.shares || 0, avg: +s0.avg || 0, T: +s0.T || 0, mode: s0.mode === 'REVERSE' ? 'REVERSE' : 'NORMAL', revFirst: !!s0.revFirst };
    if (!st.shares) { st.avg = 0; st.T = 0; st.mode = 'NORMAL'; st.revFirst = false; }
    const recs = (tr.records || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    const recMap = new Map(recs.map((r) => [r.date, r]));
    const lastRec = recs.length ? recs[recs.length - 1].date : null;
    const begin = s0.begin;
    const dset = new Set();
    for (const b of bars) if (b.date >= begin && lastRec && b.date <= lastRec) dset.add(b.date);
    for (const r of recs) if (r.date >= begin) dset.add(r.date);
    const dates = [...dset].sort();
    const barMap = new Map(bars.map((b) => [b.date, b]));

    let closes = bars.filter((b) => b.date < begin).slice(-5).map((b) => b.close);
    const days = [], cycles = [];
    let cyc = null, cumRealized = 0;
    if (st.shares > 0) cyc = { no: 1, start: begin, startCapital: st.cash + st.shares * st.avg, mid: true, maxT: st.T, reverseEntries: 0, days: 0, realized: 0 };
    const warnings = [];

    for (const date of dates) {
      const b = barMap.get(date), rec = recMap.get(date);
      if (b && b.split && b.split !== 1) {
        const r = b.split;
        closes = closes.map((c) => c / r);
        if (st.shares) { st.shares = Math.floor(st.shares * r + 1e-9); st.avg /= r; }
      }
      const close = rec && rec.close > 0 ? +rec.close : b ? b.close : null;
      const prevClose = closes.length ? closes[closes.length - 1] : close;
      const tick = prevClose >= 2 ? 0.01 : 0;
      const sma5 = closes.length ? closes.reduce((a, c) => a + c, 0) / closes.length : prevClose;
      const before = Object.assign({}, st);
      const o = E.makeOrders(st, cfg, tick, sma5, st.cash);
      const res = applyDay(st, o, rec ? rec.fills || [] : [], close, cfg);
      const adj = rec && rec.adjust;
      if (adj) {
        if (adj.cashDelta) st.cash += +adj.cashDelta;
        if (adj.shares != null && adj.shares !== '') st.shares = Math.max(0, Math.floor(+adj.shares));
        if (adj.avg != null && adj.avg !== '') st.avg = +adj.avg;
        if (adj.T != null && adj.T !== '') st.T = +adj.T;
        if (adj.mode) { st.mode = adj.mode; st.revFirst = adj.mode === 'REVERSE' ? !!adj.revFirst : false; }
        if (!st.shares) { st.avg = 0; }
        res.events.push('수동 보정');
      }
      if (res.cycleStart) cyc = { no: cycles.length + 1, start: date, startCapital: before.cash, mid: false, maxT: 1, reverseEntries: 0, days: 0, realized: 0 };
      if (res.events.includes('리버스 진입') && cyc) cyc.reverseEntries++;
      cumRealized += res.realized;
      let ended = null;
      if (cyc) {
        cyc.days++; cyc.realized += res.realized; cyc.maxT = Math.max(cyc.maxT, st.T);
        if (res.cycleEnd) {
          Object.assign(cyc, { end: date, endCash: st.cash, profit: st.cash - cyc.startCapital, retPct: (st.cash / cyc.startCapital - 1) * 100 });
          cycles.push(cyc); ended = cyc; cyc = null;
        }
      }
      if (st.cash < -0.5) warnings.push(`${date}: 잔금이 마이너스(${st.cash.toFixed(2)})가 됐습니다. 체결 기록을 확인하세요.`);
      if (close > 0) { closes.push(close); if (closes.length > 5) closes.shift(); }
      days.push({ date, recorded: !!rec, close, o, before, after: Object.assign({}, st), fills: res.fills, events: res.events,
        realized: res.realized, cumRealized, cycleEnd: ended, cycleNo: ended ? ended.no : cyc ? cyc.no : null, note: rec ? rec.note || '' : '',
        equity: close > 0 ? st.cash + st.shares * close : null, adjust: adj || null });
    }

    // 다음에 기록할 날: 마지막 처리일 이후의 거래일(데이터에 있으면 그날, 없으면 다음 평일)
    const lastDone = dates.length ? dates[dates.length - 1] : null;
    const after = lastDone || addDays(begin, -1);
    const missing = bars.filter((b) => b.date > after && b.date >= begin).map((b) => b.date);
    const nextDate = missing.length ? missing[0] : (lastDone ? nextWeekday(lastDone) : begin);
    // 다음 거래일에 액면분할이 있으면 그 날 주문은 분할 반영 후 기준
    const nextState = Object.assign({}, st);
    let closesN = closes.slice();
    const nb = barMap.get(nextDate);
    if (nb && nb.split && nb.split !== 1) {
      closesN = closesN.map((c) => c / nb.split);
      if (nextState.shares) { nextState.shares = Math.floor(nextState.shares * nb.split + 1e-9); nextState.avg /= nb.split; }
    }
    const lastClose = closesN.length ? closesN[closesN.length - 1] : null;
    const tickN = lastClose >= 2 ? 0.01 : 0;
    const sma5 = closesN.length ? closesN.reduce((a, c) => a + c, 0) / closesN.length : lastClose;
    const nextOrders = E.makeOrders(nextState, cfg, tickN, sma5, nextState.cash);
    const lastBar = bars[bars.length - 1];
    const markClose = lastBar && (!lastDone || lastBar.date >= lastDone) ? lastBar.close : lastClose;
    const markDate = lastBar && (!lastDone || lastBar.date >= lastDone) ? lastBar.date : lastDone;
    return { cfg, state: Object.assign({}, st), nextState, splitNext: nb && nb.split !== 1 ? nb.split : null, days, cycles, openCycle: cyc, cumRealized, lastDone, nextDate, missing, nextOrders,
      markClose, markDate, warnings, sma5 };
  }

  // 중간 진입: 원금이 1회씩 고르게 들어갔다고 보고 T를 어림한다(T ≈ n × 매입금 / (매입금 + 현금))
  function estimateT(n, cash, shares, avg) {
    const inv = shares * avg;
    if (!(inv > 0)) return 0;
    return Math.round((n * inv / (inv + Math.max(0, cash))) * 100) / 100;
  }

  root.MubaeTracker = { ROLES, cfgOf, orderRows, autoJudge, applyDay, replay, estimateT, nextWeekday, addDays };
})(typeof window !== 'undefined' ? window : globalThis);
