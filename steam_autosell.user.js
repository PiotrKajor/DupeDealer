// ==UserScript==
// @name         DupeDealer — auto-wystawianie duplikatów (userscript)
// @namespace    https://github.com/PiotrKajor/DupeDealer
// @version      1.0.0
// @description  Wystawia duplikaty kart/przedmiotów Steam z poziomu przeglądarki, w Twojej sesji. Wycena hurtem (multisell), odstęp między ofertami, dry-run domyślnie. NIE potwierdza — potwierdzasz w apce Steam Mobile.
// @match        https://steamcommunity.com/id/*/inventory*
// @match        https://steamcommunity.com/profiles/*/inventory*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/* Działa na TWOJEJ stronie ekwipunku (musisz być zalogowany). Reużywa logikę
   DupeDealera, ale wszystko leci przez sesję przeglądarki — czyli z tego samego
   miejsca, gdzie ręczna sprzedaż u Ciebie działa. Zero pobierania cen po jednej
   pozycji (to właśnie ściągało limit 429): ceny idą jednym żądaniem `multisell`. */
(function () {
  'use strict';

  // ----------------------------------------------------------- KONFIGURACJA ---
  const APP = '753/6';        // 753/6=karty, 440/2=TF2, 730/2=CS2, 570/2=Dota2
  const TYPES = 'Trading Card'; // filtr typu; '' = wszystkie marketable duplikaty (TF2/CS)
  const UNDERCUT = 0;         // o ile groszy zejść poniżej ceny kupującego
  const DELAY_MS = 2500;      // odstęp między wystawieniami (ms)
  const MULTISELL_BATCH = 40; // ile nazw na jedno żądanie wyceny
  // ---------------------------------------------------------------------------

  const [APPID, CONTEXTID] = APP.split('/');
  const SESSIONID = window.g_sessionID;
  const STEAMID = window.g_steamID ||
    (window.g_rgProfileData && window.g_rgProfileData.steamid) ||
    (window.g_ActiveUser && window.g_ActiveUser.strSteamId);

  // ---------------------------------------------------------------- logika ----
  function parsePrice(s) {
    let m = String(s).match(/(\d+)[.,](\d{2})/);
    if (m) return parseInt(m[1], 10) * 100 + parseInt(m[2], 10);
    m = String(s).match(/(\d+)/);
    return m ? parseInt(m[1], 10) * 100 : 0;
  }

  function buyerPriceToReceive(buyer) {
    // ile masz dostać, by kupujący zapłacił <= buyer (prowizja Steam ~15%, min 1+1)
    for (let r = buyer; r > 0; r--) {
      if (r + Math.max(1, Math.floor(r * 5 / 100)) + Math.max(1, Math.floor(r * 10 / 100)) <= buyer) return r;
    }
    return 0;
  }

  function fmt(cents) {
    return (Math.floor(cents / 100)) + ',' + String(cents % 100).padStart(2, '0');
  }

  async function fetchInventory() {
    const url = `https://steamcommunity.com/inventory/${STEAMID}/${APPID}/${CONTEXTID}?l=english&count=2000`;
    const r = await fetch(url, { credentials: 'include' });
    if (r.status === 429) throw new Error('429 na ekwipunku (poczekaj chwilę)');
    return r.json();
  }

  function pickDuplicates(inv) {
    const desc = {};
    for (const d of inv.descriptions) desc[d.classid + '_' + d.instanceid] = d;
    const wanted = TYPES.split(',').map(t => t.trim()).filter(Boolean);
    const items = [];
    for (const a of inv.assets) {
      const d = desc[a.classid + '_' + a.instanceid];
      if (!d) continue;
      const typ = d.type || '';
      if (d.marketable && (!wanted.length || wanted.some(w => typ.includes(w)))) {
        items.push({ assetid: a.assetid, name: d.market_hash_name, contextid: a.contextid || CONTEXTID });
      }
    }
    const counts = {};
    for (const it of items) counts[it.name] = (counts[it.name] || 0) + 1;
    const seen = {}, toSell = [];
    for (const it of items) {                    // zostaw jeden z każdego rodzaju
      seen[it.name] = seen[it.name] || 0;
      if (seen[it.name] < counts[it.name] - 1) { toSell.push(it); seen[it.name]++; }
    }
    return { total: items.length, kinds: Object.keys(counts).length, toSell };
  }

  function parseMultisell(html, requested) {
    // Nazwy i ceny w dwóch osobnych blokach, powiązane WYŁĄCZNIE kolejnością.
    // Jak długości/zestawy się nie zgadzają — zwracamy pustkę, żeby nie wystawić
    // karty po cudzej cenie (zgadywanie = realna strata).
    const names = [...html.matchAll(/"market_hash_name":"((?:[^"\\]|\\.)*)"/g)]
      .map(m => JSON.parse('"' + m[1] + '"'));
    const prices = [...html.matchAll(/name="sell_\d+_price_paid"[^>]*?value="([^"]*)"/g)]
      .map(m => m[1]);
    if (!names.length || names.length !== prices.length) return {};
    const reqSet = new Set(requested), nameSet = new Set(names);
    if (reqSet.size !== nameSet.size || [...reqSet].some(n => !nameSet.has(n))) return {};
    const out = {};
    for (let i = 0; i < names.length; i++) { const c = parsePrice(prices[i]); if (c) out[names[i]] = c; }
    return out;
  }

  async function fetchPrices(names) {
    const out = {};
    for (let i = 0; i < names.length; i += MULTISELL_BATCH) {
      const chunk = names.slice(i, i + MULTISELL_BATCH);
      const params = new URLSearchParams({ appid: APPID, contextid: CONTEXTID });
      for (const n of chunk) params.append('items[]', n);
      const r = await fetch('https://steamcommunity.com/market/multisell?' + params.toString(),
        { credentials: 'include' });
      if (r.status === 429) throw new Error('429 na wycenie (multisell) — poczekaj chwilę');
      Object.assign(out, parseMultisell(await r.text(), chunk));
    }
    return out;
  }

  async function sellItem(assetid, contextid, receiveCents) {
    const body = new URLSearchParams({
      sessionid: SESSIONID, appid: APPID, contextid: String(contextid),
      assetid: String(assetid), amount: '1', price: String(receiveCents),
    });
    const r = await fetch('https://steamcommunity.com/market/sellitem/', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' },
      body,
    });
    return r.json();
  }

  const sleep = ms => new Promise(res => setTimeout(res, ms));

  // ------------------------------------------------------------- przebieg -----
  async function run(dry) {
    if (!SESSIONID || !STEAMID) { log('Brak sesji — odśwież stronę ekwipunku i zaloguj się.', '#ff5d6c'); return; }
    setBusy(true);
    try {
      log(`— ${dry ? 'PODGLĄD (dry-run)' : 'WYSTAWIANIE'} — ekwipunek ${APP}, typy "${TYPES || 'wszystkie'}" —`, '#4fb4ff');
      const inv = await fetchInventory();
      if (!inv || !inv.assets) { log('Pusty/niedostępny ekwipunek.', '#ff5d6c'); return; }
      const { total, kinds, toSell } = pickDuplicates(inv);
      log(`Marketable: ${total}, rodzajów: ${kinds}, duplikatów do sprzedania: ${toSell.length}`);
      if (!toSell.length) { log('Brak duplikatów.', '#ffb454'); return; }

      const names = [...new Set(toSell.map(t => t.name))];
      log(`Wyceniam hurtem ${names.length} nazw…`);
      const prices = await fetchPrices(names);

      let ok = 0, fail = 0, skip = 0, sum = 0;
      for (let i = 0; i < toSell.length; i++) {
        const it = toSell[i];
        const buyer = prices[it.name];
        const receive = buyer ? buyerPriceToReceive(buyer - UNDERCUT) : 0;
        if (!buyer || receive <= 0) {
          log(`  ! ${it.name} — brak/za niska cena, pomijam`, '#ffb454'); skip++; continue;
        }
        if (dry) {
          log(`  ${it.name}: kupujący ${fmt(buyer)} → dostajesz ${fmt(receive)} (asset ${it.assetid}) [dry-run]`);
          sum += receive; continue;
        }
        try {
          const resp = await sellItem(it.assetid, it.contextid, receive);
          if (resp && resp.success) { log(`  ✓ ${it.name} — dostajesz ${fmt(receive)}`, '#3ddc84'); ok++; sum += receive; }
          else { log(`  ✗ ${it.name} — ${(resp && resp.message) || 'błąd'}`, '#ff5d6c'); fail++; }
        } catch (e) { log(`  ✗ ${it.name} — ${e.message}`, '#ff5d6c'); fail++; }
        if (i < toSell.length - 1) await sleep(DELAY_MS);
      }

      if (dry) log(`Razem (podgląd): ${toSell.length - skip} ofert, dostałbyś ~${fmt(sum)} zł.`, '#4fb4ff');
      else {
        log(`Wystawiono ${ok} (błędów ${fail}, pominięto ${skip}), razem ~${fmt(sum)} zł.`, '#4fb4ff');
        log('TERAZ: apka Steam Mobile → Potwierdzenia → Zatwierdź wszystko.', '#4fb4ff');
      }
    } catch (e) {
      log('Przerwane: ' + e.message, '#ff5d6c');
    } finally { setBusy(false); }
  }

  // --------------------------------------------------------------- panel UI ---
  let logEl, btnDry, btnSell;
  function log(text, color) {
    const line = document.createElement('div');
    line.textContent = text;
    if (color) line.style.color = color;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function setBusy(b) { btnDry.disabled = b; btnSell.disabled = b; }

  function buildPanel() {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;right:16px;bottom:16px;width:360px;z-index:99999;background:#181c26;color:#e8ecf4;border:1px solid #2a3245;border-radius:12px;padding:12px;font:12px/1.5 Segoe UI,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.5)';
    box.innerHTML = '<b style="font-size:14px">DupeDealer — duplikaty → rynek</b>' +
      `<div style="color:#8b94a7;margin:2px 0 8px">${APP} · undercut ${UNDERCUT}gr · odstęp ${DELAY_MS / 1000}s · bot NIE potwierdza</div>`;
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;margin-bottom:8px';
    btnDry = mkBtn('Podgląd (dry-run)', '#2a3143');
    btnSell = mkBtn('Wystaw duplikaty', '#3798e2');
    btnDry.onclick = () => run(true);
    btnSell.onclick = () => { if (confirm('Wystawić duplikaty na rynku? Każdą ofertę i tak potwierdzisz w apce Steam Mobile.')) run(false); };
    row.append(btnDry, btnSell); box.appendChild(row);
    logEl = document.createElement('div');
    logEl.style.cssText = 'height:200px;overflow:auto;background:#0c0f15;border:1px solid #2a3245;border-radius:8px;padding:6px;font-family:Consolas,monospace';
    box.appendChild(logEl);
    document.body.appendChild(box);
    log('Gotowe. „Podgląd" pokaże co i za ile, nic nie wystawiając.', '#8b94a7');
  }
  function mkBtn(label, bg) {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = `flex:1;padding:7px 10px;border:none;border-radius:8px;background:${bg};color:#fff;font-weight:600;cursor:pointer`;
    return b;
  }

  if (document.body) buildPanel();
})();
