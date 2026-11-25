/* 무매 V4 기록장 - 파일 동기화
 * 원드라이브·구글 드라이브 같은 동기화 폴더의 JSON 파일을 브라우저가 직접 읽고 쓴다(File System Access API).
 * 서버는 없다. 여러 PC가 같은 파일을 쓰므로, 마지막으로 맞춘 상태(base)를 기준으로 3방향 병합한다.
 *  - 한쪽만 바뀐 것은 그쪽을 따른다(추가·수정·삭제 모두).
 *  - 양쪽이 같은 항목을 서로 다르게 바꾼 경우만 충돌로 보고, 호출하는 쪽이 고른 편(prefer)을 따른다.
 * 병합 단위: 무매별 설정(기록 제외) 1개 + 날짜별 기록 1개씩.
 */
(function (root) {
  'use strict';
  const APP = 'mubae-v4-tracker';

  // 키 순서와 무관한 비교용 문자열
  function stable(v) {
    if (v === undefined) return 'undefined';
    if (v === null || typeof v !== 'object') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
    return '{' + Object.keys(v).filter((k) => v[k] !== undefined).sort().map((k) => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
  }
  const same = (a, b) => stable(a) === stable(b);
  const meta = (t) => { if (!t) return undefined; const o = Object.assign({}, t); delete o.records; return o; };
  const recMap = (t) => { const m = new Map(); if (t) for (const r of t.records || []) m.set(r.date, r); return m; };
  const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));

  // 3방향 선택: 결과값과 충돌 여부
  function pick(b, l, r) {
    if (same(l, r)) return { v: l, c: false };
    if (same(l, b)) return { v: r, c: false };
    if (same(r, b)) return { v: l, c: false };
    return { v: undefined, c: true };
  }

  /**
   * base·local·remote: 무매 배열(trackers). base가 없으면(처음 연결) null.
   * prefer: 충돌 때 'local' | 'remote'.
   * 반환: { trackers, conflicts: [{trackerId, name, date|null, kind}], kept: [...] }
   */
  function merge3(base, local, remote, prefer) {
    const B = new Map((base || []).map((t) => [t.id, t]));
    const L = new Map((local || []).map((t) => [t.id, t]));
    const Rm = new Map((remote || []).map((t) => [t.id, t]));
    const ids = [];
    for (const t of local || []) ids.push(t.id);
    for (const t of remote || []) if (!L.has(t.id)) ids.push(t.id);
    const out = [], conflicts = [], kept = [];

    for (const id of ids) {
      const b = B.get(id), l = L.get(id), r = Rm.get(id);
      const name = (l || r).name;
      // 한쪽에만 있는 무매: 상대가 지웠는지(base에 있었는지), 새로 만든 것인지
      if (!l || !r) {
        const only = l || r;
        if (!b) { out.push(clone(only)); continue; }                 // 새로 만든 무매
        if (same(only, b)) continue;                                // 상대가 지웠고 이쪽은 그대로 → 삭제
        kept.push({ trackerId: id, name, why: '한쪽에서 지웠지만 다른 쪽에서 고쳐서 남김' });
        out.push(clone(only));                                      // 지운 쪽보다 고친 쪽을 남긴다(기록 보존)
        continue;
      }
      // 설정
      const pm = pick(meta(b), meta(l), meta(r));
      let m = pm.v;
      if (pm.c) { conflicts.push({ trackerId: id, name, date: null, kind: 'meta' }); m = prefer === 'remote' ? meta(r) : meta(l); }
      // 날짜별 기록
      const mb = recMap(b), ml = recMap(l), mr = recMap(r);
      const dates = new Set([...ml.keys(), ...mr.keys(), ...mb.keys()]);
      const recs = [];
      for (const d of dates) {
        const p = pick(mb.get(d), ml.get(d), mr.get(d));
        let v = p.v;
        if (p.c) { conflicts.push({ trackerId: id, name, date: d, kind: 'record' }); v = prefer === 'remote' ? mr.get(d) : ml.get(d); }
        if (v !== undefined) recs.push(clone(v));
      }
      recs.sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : 0));
      out.push(Object.assign(clone(m), { records: recs }));
    }
    return { trackers: out, conflicts, kept };
  }

  // 파일 내용 검사(백업 파일과 같은 형식)
  function parseFile(text) {
    if (!text || !text.trim()) return { empty: true, trackers: [] };
    let j;
    try { j = JSON.parse(text); } catch (e) { return { error: 'JSON 형식이 아닙니다' }; }
    if (!j || j.app !== APP || !Array.isArray(j.trackers)) return { error: '무매 기록장 파일이 아닙니다' };
    const ok = j.trackers.every((t) => t && t.id && t.ticker && t.n && t.start && t.start.begin && Array.isArray(t.records));
    if (!ok) return { error: '기록장 파일 형식이 올바르지 않습니다' };
    return { trackers: j.trackers, savedAt: j.savedAt || j.exportedAt || null };
  }
  function fileText(trackers) {
    return JSON.stringify({ app: APP, version: 1, savedAt: new Date().toISOString(), trackers }, null, 1);
  }

  // ---------- 파일 핸들 보관(IndexedDB) ----------
  const IDB = 'mubae-tracker-fs', ST = 'h';
  function idb() {
    return new Promise((res, rej) => {
      const q = indexedDB.open(IDB, 1);
      q.onupgradeneeded = () => q.result.createObjectStore(ST);
      q.onsuccess = () => res(q.result);
      q.onerror = () => rej(q.error);
    });
  }
  async function idbDo(mode, fn) {
    const db = await idb();
    return new Promise((res, rej) => {
      const tx = db.transaction(ST, mode);
      const q = fn(tx.objectStore(ST));
      tx.oncomplete = () => { db.close(); res(q && q.result); };
      tx.onerror = () => { db.close(); rej(tx.error); };
    });
  }
  const getHandle = () => idbDo('readonly', (s) => s.get('file')).catch(() => null);
  const putHandle = (h) => idbDo('readwrite', (s) => s.put(h, 'file'));
  const delHandle = () => idbDo('readwrite', (s) => s.delete('file')).catch(() => null);

  const supported = () => typeof root.showOpenFilePicker === 'function' && typeof root.showSaveFilePicker === 'function' && 'indexedDB' in root;
  const PICK = { types: [{ description: '무매 기록장 동기화 파일', accept: { 'application/json': ['.json'] } }] };
  async function pickNew() { return root.showSaveFilePicker(Object.assign({ suggestedName: 'mubae-tracker.json', id: 'mubae-sync' }, PICK)); }
  async function pickExisting() { const [h] = await root.showOpenFilePicker(Object.assign({ multiple: false, id: 'mubae-sync' }, PICK)); return h; }
  async function permission(h, ask) {
    const o = { mode: 'readwrite' };
    if ((await h.queryPermission(o)) === 'granted') return true;
    if (!ask) return false;
    return (await h.requestPermission(o)) === 'granted';
  }
  async function readFile(h) { const f = await h.getFile(); return { text: await f.text(), lastModified: f.lastModified }; }
  async function writeFile(h, text) {
    const w = await h.createWritable();
    await w.write(text);
    await w.close();
    return (await h.getFile()).lastModified;
  }

  root.MubaeSync = { APP, stable, same, merge3, parseFile, fileText, supported, getHandle, putHandle, delHandle, pickNew, pickExisting, permission, readFile, writeFile };
})(typeof window !== 'undefined' ? window : globalThis);
