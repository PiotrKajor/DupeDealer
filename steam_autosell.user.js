// ==UserScript==
// @name         DupeDealer — auto-wystawianie duplikatów (userscript)
// @namespace    https://github.com/PiotrKajor/DupeDealer
// @version      1.1.0
// @description  Wystawia duplikaty kart/przedmiotów Steam z poziomu przeglądarki, w Twojej sesji. Wybierasz ekwipunek, typy i konkretne pozycje klikając w panelu. Wycena hurtem (multisell), odstęp między ofertami. NIE potwierdza — potwierdzasz w apce Steam Mobile.
// @match        https://steamcommunity.com/id/*/inventory*
// @match        https://steamcommunity.com/profiles/*/inventory*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/* Działa na TWOJEJ stronie ekwipunku (musisz być zalogowany). Reużywa logikę
   DupeDealera, ale wszystko leci przez sesję przeglądarki — czyli z tego samego
   miejsca, gdzie ręczna sprzedaż u Ciebie działa. Zero pobierania cen po jednej
   pozycji (to właśnie ściągało limit 429): ceny idą jednym żądaniem `multisell`.

   Panel: wybierasz ekwipunek i typy przedmiotów przyciskami, skanujesz, a potem
   odznaczasz to, czego sprzedać nie chcesz (i ustawiasz ile sztuk). Wystawiane
   jest wyłącznie to, co zostało zaznaczone. */
(function () {
  'use strict';

  // ----------------------------------------------------------- KONFIGURACJA ---
  // Ekwipunki do wyboru w panelu; `types` to gotowe filtry typu (przyciski).
  const APPS = [
    {
      id: 'cards', label: 'Karty Steam', appid: '753', contextid: '6',
      types: [
        { label: 'Karty', value: 'Trading Card', on: true },
        { label: 'Emotikony', value: 'Emoticon' },
        { label: 'Tła profilu', value: 'Profile Background' },
        { label: 'Boostery', value: 'Booster Pack' },
      ],
    },
    { id: 'tf2', label: 'TF2', appid: '440', contextid: '2', types: [] },
    { id: 'cs2', label: 'CS2', appid: '730', contextid: '2', types: [] },
    { id: 'dota2', label: 'Dota 2', appid: '570', contextid: '2', types: [] },
  ];
  const MULTISELL_BATCH = 40;   // ile nazw na jedno żądanie wyceny
  const STORE_KEY = 'dupedealer_userscript_settings_v1';
  // ---------------------------------------------------------------------------

  const SESSIONID = window.g_sessionID;
  const STEAMID = window.g_steamID ||
    (window.g_rgProfileData && window.g_rgProfileData.steamid) ||
    (window.g_ActiveUser && window.g_ActiveUser.strSteamId);

  // Ustawienia panelu — trzymane w localStorage, żeby nie klikać tego samego
  // przy każdym wejściu na ekwipunek.
  const settings = Object.assign({
    appId: 'cards',
    types: { cards: ['Trading Card'] },   // per ekwipunek: zaznaczone filtry typu
    custom: {},                            // per ekwipunek: własny filtr (po przecinku)
    undercut: 0,                           // o ile groszy zejść poniżej ceny kupującego
    delay: 2.5,                            // odstęp między wystawieniami (s)
    collapsed: false,
    pos: null,                             // {left, top} po przeciągnięciu panelu
  }, loadSettings());

  function loadSettings() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveSettings() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(settings)); } catch (e) { /* prywatne okno */ }
  }

  const state = {
    rows: [],        // wynik skanu: pozycje pogrupowane po nazwie
    busy: false,
    abort: false,
    currency: 'zł',
  };

  function currentApp() {
    return APPS.find(a => a.id === settings.appId) || APPS[0];
  }
  function activeTypes() {
    const app = currentApp();
    const picked = settings.types[app.id] || [];
    const custom = (settings.custom[app.id] || '').split(',').map(s => s.trim()).filter(Boolean);
    return [...new Set([...picked, ...custom])];
  }

  // ---------------------------------------------------------------- logika ----
  function parsePrice(s) {
    let m = String(s).match(/(\d+)[.,](\d{2})/);
    if (m) return parseInt(m[1], 10) * 100 + parseInt(m[2], 10);
    m = String(s).match(/(\d+)/);
    return m ? parseInt(m[1], 10) * 100 : 0;
  }

  function detectCurrency(s) {
    // z „12,34 zł" / „$1.23" zostaje sam symbol — tylko do wyświetlania
    const sym = String(s).replace(/[\d.,\s -]/g, '').trim();
    return sym || state.currency;
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
  function money(cents) { return fmt(cents) + ' ' + state.currency; }

  async function fetchInventory() {
    const app = currentApp();
    const url = `https://steamcommunity.com/inventory/${STEAMID}/${app.appid}/${app.contextid}?l=english&count=2000`;
    const r = await fetch(url, { credentials: 'include' });
    if (r.status === 429) throw new Error('429 na ekwipunku (poczekaj chwilę)');
    return r.json();
  }

  function groupDuplicates(inv) {
    const app = currentApp();
    const desc = {};
    for (const d of inv.descriptions) desc[d.classid + '_' + d.instanceid] = d;
    const wanted = activeTypes();
    const items = [];
    for (const a of inv.assets) {
      const d = desc[a.classid + '_' + a.instanceid];
      if (!d) continue;
      const typ = d.type || '';
      if (d.marketable && (!wanted.length || wanted.some(w => typ.includes(w)))) {
        items.push({
          assetid: a.assetid, name: d.market_hash_name, type: typ,
          icon: d.icon_url || '', contextid: a.contextid || app.contextid,
        });
      }
    }
    // grupujemy po nazwie; jeden egzemplarz każdego rodzaju zostaje w ekwipunku
    const groups = new Map();
    for (const it of items) {
      let g = groups.get(it.name);
      if (!g) { g = { name: it.name, type: it.type, icon: it.icon, assets: [] }; groups.set(it.name, g); }
      g.assets.push(it);
    }
    const rows = [];
    for (const g of groups.values()) {
      const dupes = g.assets.length - 1;
      if (dupes < 1) continue;
      rows.push({
        name: g.name, type: g.type, icon: g.icon,
        assets: g.assets.slice(0, dupes),   // sprzedajemy nadmiar, jeden zostaje
        total: g.assets.length, dupes, sell: dupes,
        checked: true, buyer: 0, receive: 0, priced: false,
      });
    }
    return { total: items.length, kinds: groups.size, rows };
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
    for (let i = 0; i < names.length; i++) {
      const c = parsePrice(prices[i]);
      if (c) { out[names[i]] = c; state.currency = detectCurrency(prices[i]); }
    }
    return out;
  }

  async function fetchPrices(names, onProgress) {
    const app = currentApp();
    const out = {};
    for (let i = 0; i < names.length; i += MULTISELL_BATCH) {
      const chunk = names.slice(i, i + MULTISELL_BATCH);
      const params = new URLSearchParams({ appid: app.appid, contextid: app.contextid });
      for (const n of chunk) params.append('items[]', n);
      const r = await fetch('https://steamcommunity.com/market/multisell?' + params.toString(),
        { credentials: 'include' });
      if (r.status === 429) throw new Error('429 na wycenie (multisell) — poczekaj chwilę');
      Object.assign(out, parseMultisell(await r.text(), chunk));
      if (onProgress) onProgress(Math.min(i + MULTISELL_BATCH, names.length), names.length);
    }
    return out;
  }

  async function sellItem(assetid, contextid, receiveCents) {
    const app = currentApp();
    const body = new URLSearchParams({
      sessionid: SESSIONID, appid: app.appid, contextid: String(contextid),
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
  function recompute() {
    // przeliczenie „dostajesz" po zmianie undercutu — bez ponownego odpytywania
    for (const row of state.rows) {
      row.receive = row.buyer ? buyerPriceToReceive(row.buyer - settings.undercut) : 0;
      if (row.priced && row.receive <= 0) row.checked = false;
    }
  }

  function selected() {
    return state.rows.filter(r => r.checked && r.priced && r.receive > 0 && r.sell > 0);
  }

  async function scan() {
    if (!requireSession()) return;
    setBusy(true);
    try {
      const app = currentApp();
      const types = activeTypes();
      log(`— SKAN — ${app.label} (${app.appid}/${app.contextid}), typy: ${types.join(', ') || 'wszystkie'} —`, 'info');
      progress(0, 1, 'Pobieram ekwipunek…');
      const inv = await fetchInventory();
      if (!inv || !inv.assets) { log('Pusty/niedostępny ekwipunek.', 'bad'); return; }

      const { total, kinds, rows } = groupDuplicates(inv);
      state.rows = rows;
      log(`Marketable: ${total}, rodzajów: ${kinds}, duplikatów do sprzedania: ${rows.reduce((s, r) => s + r.dupes, 0)}`);
      if (!rows.length) { log('Brak duplikatów w tym filtrze.', 'warn'); render(); return; }

      const names = rows.map(r => r.name);
      log(`Wyceniam hurtem ${names.length} nazw…`);
      const prices = await fetchPrices(names, (done, all) => progress(done, all, `Wycena ${done}/${all}`));
      let noPrice = 0;
      for (const row of rows) {
        row.buyer = prices[row.name] || 0;
        row.priced = row.buyer > 0;
        if (!row.priced) { row.checked = false; noPrice++; }
      }
      recompute();
      rows.sort((a, b) => (b.receive * b.sell) - (a.receive * a.sell));
      if (noPrice) log(`Bez ceny (pomijam): ${noPrice}`, 'warn');
      log('Zaznacz/odznacz pozycje na liście i kliknij „Wystaw zaznaczone".', 'dim');
      showTab('list');
    } catch (e) {
      log('Przerwane: ' + e.message, 'bad');
    } finally {
      progress(0, 0, '');
      setBusy(false);
      render();
    }
  }

  function dryRun() {
    const rows = selected();
    if (!rows.length) { log('Nic nie zaznaczono — najpierw zeskanuj i zaznacz pozycje.', 'warn'); showTab('log'); return; }
    let sum = 0;
    log(`— PODGLĄD (dry-run) — ${rows.length} pozycji —`, 'info');
    for (const r of rows) {
      log(`  ${r.name} ×${r.sell}: kupujący ${money(r.buyer)} → dostajesz ${money(r.receive)}/szt.`);
      sum += r.receive * r.sell;
    }
    log(`Razem ${rows.reduce((s, r) => s + r.sell, 0)} ofert, dostałbyś ~${money(sum)}. Nic nie wystawiono.`, 'info');
    showTab('log');
  }

  async function sellSelected() {
    if (!requireSession()) return;
    const rows = selected();
    if (!rows.length) return;
    const queue = [];
    for (const r of rows) for (const a of r.assets.slice(0, r.sell)) queue.push({ row: r, asset: a });

    setBusy(true);
    state.abort = false;
    showTab('log');
    try {
      log(`— WYSTAWIANIE — ${queue.length} ofert, odstęp ${settings.delay}s —`, 'info');
      let ok = 0, fail = 0, sum = 0;
      for (let i = 0; i < queue.length; i++) {
        if (state.abort) { log('Zatrzymane przez Ciebie.', 'warn'); break; }
        const { row, asset } = queue[i];
        progress(i, queue.length, `Wystawiam ${i + 1}/${queue.length}`);
        try {
          const resp = await sellItem(asset.assetid, asset.contextid, row.receive);
          if (resp && resp.success) { log(`  ✓ ${row.name} — dostajesz ${money(row.receive)}`, 'good'); ok++; sum += row.receive; }
          else { log(`  ✗ ${row.name} — ${(resp && resp.message) || 'błąd'}`, 'bad'); fail++; }
        } catch (e) { log(`  ✗ ${row.name} — ${e.message}`, 'bad'); fail++; }
        if (i < queue.length - 1 && !state.abort) await sleep(settings.delay * 1000);
      }
      log(`Wystawiono ${ok} (błędów ${fail}), razem ~${money(sum)}.`, 'info');
      log('TERAZ: apka Steam Mobile → Potwierdzenia → Zatwierdź wszystko.', 'info');
    } catch (e) {
      log('Przerwane: ' + e.message, 'bad');
    } finally {
      progress(0, 0, '');
      setBusy(false);
    }
  }

  function requireSession() {
    if (!SESSIONID || !STEAMID) {
      log('Brak sesji — odśwież stronę ekwipunku i zaloguj się.', 'bad');
      showTab('log');
      return false;
    }
    return true;
  }

  // --------------------------------------------------------------- panel UI ---
  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: 'Segoe UI', Inter, system-ui, sans-serif; }
    .panel {
      position: fixed; right: 16px; bottom: 16px; width: 380px; max-height: 84vh;
      display: flex; flex-direction: column; z-index: 2147483000;
      background: #11141b; color: #e8ecf4; border: 1px solid #2a3245; border-radius: 14px;
      box-shadow: 0 18px 48px rgba(0,0,0,.55); font-size: 12px; line-height: 1.5;
      overflow: hidden;
    }
    .panel.collapsed .body { display: none; }
    .panel.collapsed { max-height: none; }
    header {
      display: flex; align-items: center; gap: 8px; padding: 10px 12px; cursor: grab;
      background: linear-gradient(180deg,#1b2030,#151925); border-bottom: 1px solid #2a3245;
      user-select: none;
    }
    header:active { cursor: grabbing; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #3ddc84; flex: none; }
    .dot.off { background: #ff5d6c; }
    .title { font-weight: 700; font-size: 13px; letter-spacing: .2px; }
    .sub { color: #8b94a7; font-size: 11px; margin-left: auto; }
    .icobtn {
      border: 1px solid #2a3245; background: #212736; color: #8b94a7; width: 22px; height: 22px;
      border-radius: 6px; cursor: pointer; font-size: 13px; line-height: 1; padding: 0;
    }
    .icobtn:hover { color: #e8ecf4; border-color: #3d4763; }
    /* body rośnie do wysokości panelu; kurczy się LISTA, żeby stopka z przyciskami
       nigdy nie uciekła poza ekran */
    .body { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; }
    section { padding: 9px 12px; border-bottom: 1px solid #1e2432; flex: none; }
    .lbl { color: #8b94a7; font-size: 10px; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 6px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      border: 1px solid #2a3245; background: #181c26; color: #b9c2d4; padding: 5px 10px;
      border-radius: 999px; cursor: pointer; font-size: 11.5px; font-weight: 600; transition: .12s;
    }
    .chip:hover { border-color: #3d4763; color: #e8ecf4; }
    .chip.on { background: rgba(79,180,255,.16); border-color: #4fb4ff; color: #cfe8ff; }
    .chip.wide { border-radius: 8px; }
    .grid { display: flex; gap: 10px; align-items: flex-end; }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field.grow { flex: 1; }
    .field label { color: #8b94a7; font-size: 10.5px; }
    input[type=number], input[type=text] {
      background: #0f1219; border: 1px solid #2a3245; color: #e8ecf4; border-radius: 8px;
      padding: 6px 8px; font-size: 12px; width: 100%; outline: none;
    }
    input:focus { border-color: #4fb4ff; }
    input[type=number] { width: 72px; }
    .btn {
      border: none; border-radius: 9px; padding: 9px 12px; font-weight: 700; font-size: 12px;
      cursor: pointer; background: #212736; color: #e8ecf4; transition: .12s; flex: 1;
    }
    .btn:hover:not(:disabled) { background: #2a3245; }
    .btn.accent { background: #3798e2; }
    .btn.accent:hover:not(:disabled) { background: #4fb4ff; }
    .btn.good { background: #2b8a5a; }
    .btn.good:hover:not(:disabled) { background: #34a76c; }
    .btn.danger { background: #8a2f38; }
    .btn:disabled { opacity: .45; cursor: not-allowed; }
    .row { display: flex; gap: 8px; }
    .tabs { display: flex; gap: 4px; padding: 8px 12px 0; flex: none; align-items: center; }
    .tab {
      background: none; border: none; color: #8b94a7; padding: 6px 10px; border-radius: 8px 8px 0 0;
      cursor: pointer; font-size: 11.5px; font-weight: 700;
    }
    .tab.on { color: #e8ecf4; background: #181c26; }
    .pane { display: none; margin: 0 12px 10px; background: #0c0f15; border: 1px solid #2a3245; border-radius: 10px; }
    .pane.on { display: flex; flex: 1 1 auto; min-height: 0; }
    #list { flex: 1 1 auto; min-height: 96px; max-height: 42vh; overflow: auto; width: 100%; }
    .item {
      display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #171c27;
      cursor: pointer;
    }
    .item:last-child { border-bottom: none; }
    .item:hover { background: #131722; }
    .item.off { opacity: .45; }
    .item img { display: none; width: 28px; height: 28px; flex: none; border-radius: 4px; }
    .item img.ok { display: block; }   /* pokazujemy dopiero, gdy ikonka się wczyta */
    .item .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .item .qty { color: #8b94a7; font-size: 11px; white-space: nowrap; }
    .item .val { color: #3ddc84; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .item .val.none { color: #ffb454; font-weight: 500; }
    .item input[type=checkbox] { accent-color: #4fb4ff; width: 14px; height: 14px; flex: none; }
    .item input[type=number] { width: 46px; padding: 2px 4px; font-size: 11px; }
    .empty { padding: 18px 10px; text-align: center; color: #8b94a7; }
    #log { flex: 1 1 auto; min-height: 110px; max-height: 42vh; overflow: auto; padding: 6px 8px;
           font-family: Consolas, 'SF Mono', monospace; font-size: 11px; width: 100%; }
    #log div { white-space: pre-wrap; word-break: break-word; }
    .info { color: #4fb4ff; } .good { color: #3ddc84; } .bad { color: #ff5d6c; }
    .warn { color: #ffb454; } .dim { color: #8b94a7; }
    footer { padding: 10px 12px; border-top: 1px solid #1e2432; background: #141822; flex: none; }
    .summary { display: flex; justify-content: space-between; margin-bottom: 8px; color: #8b94a7; font-size: 11.5px; }
    .summary b { color: #e8ecf4; }
    .bar { height: 5px; background: #1e2432; border-radius: 999px; overflow: hidden; margin-bottom: 8px; display: none; }
    .bar.on { display: block; }
    .bar i { display: block; height: 100%; background: #4fb4ff; width: 0; transition: width .2s; }
    .note { color: #8b94a7; font-size: 10.5px; margin-top: 8px; text-align: center; }
    .confirm { background: rgba(255,93,108,.1); border: 1px solid #8a2f38; border-radius: 9px; padding: 8px; }
    .confirm p { margin: 0 0 8px; color: #ffd7db; font-size: 11.5px; }
  `;

  let root, panel, els = {}, tab = 'list';

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const kid of kids) if (kid) el.append(kid);
    return el;
  }

  function log(text, cls) {
    const line = h('div', { text, class: cls || '' });
    els.log.appendChild(line);
    els.log.scrollTop = els.log.scrollHeight;
  }

  function progress(done, all, label) {
    els.bar.classList.toggle('on', all > 0);
    els.bar.firstChild.style.width = all > 0 ? Math.round(done / all * 100) + '%' : '0';
    if (label) els.hint.textContent = label;
    else if (!all) els.hint.textContent = '';
  }

  function setBusy(b) {
    // w trakcie pracy zostaje sam „Stop" — nie ma po co pchać zajętych przycisków
    state.busy = b;
    els.stop.style.display = b ? '' : 'none';
    els.dry.style.display = els.sell.style.display = b ? 'none' : '';
    render();
  }

  function showTab(name) {
    tab = name;
    for (const [key, el] of Object.entries(els.tabs)) el.classList.toggle('on', key === name);
    els.listPane.classList.toggle('on', name === 'list');
    els.logPane.classList.toggle('on', name === 'log');
  }

  function buildPanel() {
    const host = h('div', {});
    host.style.cssText = 'all:initial';
    root = host.attachShadow({ mode: 'open' });
    root.append(h('style', { text: CSS }));

    panel = h('div', { class: 'panel' + (settings.collapsed ? ' collapsed' : '') });

    // --- nagłówek (przeciąganie + zwijanie) ---
    const okSession = !!(SESSIONID && STEAMID);
    const head = h('header', {},
      h('span', { class: 'dot' + (okSession ? '' : ' off') }),
      h('span', { class: 'title', text: 'DupeDealer' }),
      h('span', { class: 'sub', text: okSession ? 'duplikaty → rynek' : 'brak sesji' }),
      h('button', {
        class: 'icobtn', title: 'Zwiń/rozwiń',
        onclick: () => {
          settings.collapsed = !settings.collapsed;
          panel.classList.toggle('collapsed', settings.collapsed);
          saveSettings();
        },
      }, document.createTextNode('–')),
    );
    makeDraggable(head);
    panel.append(head);

    const body = h('div', { class: 'body' });

    // --- wybór ekwipunku ---
    els.appChips = h('div', { class: 'chips' });
    body.append(h('section', {}, h('div', { class: 'lbl', text: 'Ekwipunek' }), els.appChips));

    // --- wybór typów (co sprzedać) ---
    els.typeChips = h('div', { class: 'chips' });
    els.custom = h('input', { type: 'text', placeholder: 'własny filtr typu, np. Emoticon, Rare' });
    els.custom.addEventListener('input', () => {
      settings.custom[currentApp().id] = els.custom.value;
      saveSettings();
      renderTypeChips();
    });
    els.custom.addEventListener('change', invalidate);
    body.append(h('section', {},
      h('div', { class: 'lbl', text: 'Co sprzedać' }),
      els.typeChips,
      h('div', { class: 'grid', style: 'margin-top:8px' },
        h('div', { class: 'field grow' }, h('label', { text: 'Filtr własny (po przecinku)' }), els.custom)),
    ));

    // --- parametry + skan ---
    els.undercut = h('input', { type: 'number', min: '0', max: '99', step: '1', value: String(settings.undercut) });
    els.undercut.addEventListener('change', () => {
      settings.undercut = Math.max(0, Math.min(99, parseInt(els.undercut.value, 10) || 0));
      els.undercut.value = String(settings.undercut);
      saveSettings(); recompute(); render();
    });
    els.delay = h('input', { type: 'number', min: '0.5', max: '30', step: '0.5', value: String(settings.delay) });
    els.delay.addEventListener('change', () => {
      settings.delay = Math.max(0.5, Math.min(30, parseFloat(els.delay.value) || 2.5));
      els.delay.value = String(settings.delay);
      saveSettings();
    });
    els.scan = h('button', { class: 'btn accent', text: 'Skanuj duplikaty', onclick: scan });
    body.append(h('section', {},
      h('div', { class: 'grid' },
        h('div', { class: 'field' }, h('label', { text: 'Undercut (gr)' }), els.undercut),
        h('div', { class: 'field' }, h('label', { text: 'Odstęp (s)' }), els.delay),
        h('div', { class: 'field grow' }, els.scan)),
    ));

    // --- lista / log ---
    els.tabs = {
      list: h('button', { class: 'tab on', text: 'Lista', onclick: () => showTab('list') }),
      log: h('button', { class: 'tab', text: 'Log', onclick: () => showTab('log') }),
    };
    els.selAll = h('button', { class: 'chip', text: 'Zaznacz wszystko', onclick: () => setAll(true) });
    els.selNone = h('button', { class: 'chip', text: 'Odznacz', onclick: () => setAll(false) });
    const tabs = h('div', { class: 'tabs' }, els.tabs.list, els.tabs.log,
      h('div', { style: 'flex:1' }), els.selAll, els.selNone);
    els.list = h('div', { id: 'list' });
    els.listPane = h('div', { class: 'pane on' }, els.list);
    els.log = h('div', { id: 'log' });
    els.logPane = h('div', { class: 'pane' }, els.log);
    body.append(tabs, els.listPane, els.logPane);

    // --- stopka: podsumowanie + akcje ---
    els.count = h('b', { text: '0 pozycji' });
    els.sum = h('b', { text: '—' });
    els.hint = h('span', { class: 'dim' });
    els.bar = h('div', { class: 'bar' }, h('i', {}));
    els.dry = h('button', { class: 'btn', text: 'Podgląd', onclick: dryRun });
    els.sell = h('button', { class: 'btn good', text: 'Wystaw zaznaczone', onclick: askConfirm });
    els.stop = h('button', { class: 'btn danger', text: 'Stop', onclick: () => { state.abort = true; } });
    els.stop.style.display = 'none';
    els.actions = h('div', { class: 'row' }, els.dry, els.sell, els.stop);
    els.footer = h('footer', {},
      h('div', { class: 'summary' }, h('span', {}, els.count, document.createTextNode(' '), els.hint),
        h('span', {}, document.createTextNode('dostaniesz ~'), els.sum)),
      els.bar, els.actions,
      h('div', { class: 'note', text: 'Bot NIE potwierdza — zatwierdzasz w apce Steam Mobile.' }),
    );
    body.append(els.footer);

    panel.append(body);
    root.append(panel);
    document.body.appendChild(host);

    if (settings.pos) { panel.style.left = settings.pos.left + 'px'; panel.style.top = settings.pos.top + 'px'; panel.style.right = 'auto'; panel.style.bottom = 'auto'; }

    renderAppChips();
    renderTypeChips();
    render();
    log('Gotowe. Wybierz ekwipunek i typy, potem „Skanuj duplikaty".', 'dim');
    if (!okSession) log('Brak sesji — odśwież stronę ekwipunku i zaloguj się.', 'bad');
  }

  function makeDraggable(handle) {
    let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    handle.addEventListener('mousedown', e => {
      if (e.target.closest('.icobtn')) return;
      const r = panel.getBoundingClientRect();
      dragging = true; sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
      panel.style.right = 'auto'; panel.style.bottom = 'auto';
      panel.style.left = ox + 'px'; panel.style.top = oy + 'px';
      e.preventDefault();
    });
    window.addEventListener('mousemove', e => {
      if (!dragging) return;
      const left = Math.max(0, Math.min(window.innerWidth - 80, ox + e.clientX - sx));
      const top = Math.max(0, Math.min(window.innerHeight - 40, oy + e.clientY - sy));
      panel.style.left = left + 'px'; panel.style.top = top + 'px';
    });
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      settings.pos = { left: parseInt(panel.style.left, 10), top: parseInt(panel.style.top, 10) };
      saveSettings();
    });
  }

  function renderAppChips() {
    els.appChips.textContent = '';
    for (const app of APPS) {
      const on = app.id === settings.appId;
      els.appChips.append(h('button', {
        class: 'chip wide' + (on ? ' on' : ''),
        text: `${app.label} (${app.appid}/${app.contextid})`,
        onclick: () => {
          if (state.busy || app.id === settings.appId) return;
          settings.appId = app.id;
          if (!settings.types[app.id]) settings.types[app.id] = app.types.filter(t => t.on).map(t => t.value);
          saveSettings();
          state.rows = [];
          renderAppChips(); renderTypeChips(); render();
          log(`Ekwipunek: ${app.label}. Kliknij „Skanuj duplikaty".`, 'dim');
        },
      }));
    }
  }

  function renderTypeChips() {
    const app = currentApp();
    const picked = settings.types[app.id] || (settings.types[app.id] = app.types.filter(t => t.on).map(t => t.value));
    els.custom.value = settings.custom[app.id] || '';
    els.typeChips.textContent = '';

    els.typeChips.append(h('button', {
      class: 'chip' + (activeTypes().length ? '' : ' on'),
      text: 'Wszystkie marketable',
      onclick: () => {
        if (state.busy) return;
        settings.types[app.id] = []; settings.custom[app.id] = '';
        saveSettings(); renderTypeChips(); invalidate();
      },
    }));
    for (const t of app.types) {
      const on = picked.includes(t.value);
      els.typeChips.append(h('button', {
        class: 'chip' + (on ? ' on' : ''), text: t.label,
        onclick: () => {
          if (state.busy) return;
          settings.types[app.id] = on ? picked.filter(v => v !== t.value) : [...picked, t.value];
          saveSettings(); renderTypeChips(); invalidate();
        },
      }));
    }
    if (!app.types.length) {
      els.typeChips.append(h('span', { class: 'dim', text: 'ten ekwipunek nie ma gotowych typów — użyj filtru własnego' }));
    }
  }

  function invalidate() {
    // po zmianie filtra stara lista już nic nie znaczy — kasujemy ją
    if (!state.rows.length) return;
    state.rows = [];
    render();
    log('Filtr zmieniony — kliknij „Skanuj duplikaty" jeszcze raz.', 'dim');
  }

  function setAll(on) {
    for (const r of state.rows) if (r.priced && r.receive > 0) r.checked = on;
    render();
  }

  function render() {
    // lista pozycji
    els.list.textContent = '';
    if (!state.rows.length) {
      els.list.append(h('div', { class: 'empty', text: state.busy ? 'Pracuję…' : 'Brak listy — kliknij „Skanuj duplikaty".' }));
    }
    for (const row of state.rows) {
      const sellable = row.priced && row.receive > 0;
      const cb = h('input', { type: 'checkbox' });
      cb.checked = row.checked && sellable;
      cb.disabled = !sellable || state.busy;
      cb.addEventListener('change', () => { row.checked = cb.checked; render(); });

      const qty = h('input', { type: 'number', min: '1', max: String(row.dupes), value: String(row.sell), title: 'ile sztuk wystawić' });
      qty.disabled = !sellable || state.busy;
      qty.addEventListener('change', () => {
        row.sell = Math.max(1, Math.min(row.dupes, parseInt(qty.value, 10) || 1));
        qty.value = String(row.sell);
        render();
      });
      qty.addEventListener('click', e => e.stopPropagation());

      let icon = null;
      if (row.icon) {
        icon = h('img', { src: `https://community.fastly.steamstatic.com/economy/image/${row.icon}/32fx32f`, loading: 'lazy' });
        icon.addEventListener('load', () => icon.classList.add('ok'));  // bez szarych kwadratów
      }

      const item = h('div', { class: 'item' + (sellable ? '' : ' off') },
        cb, icon,
        h('span', { class: 'nm', text: row.name, title: row.name + (row.type ? ' — ' + row.type : '') }),
        qty,
        h('span', { class: 'qty', text: `z ${row.dupes}` }),
        sellable
          ? h('span', {
              class: 'val', text: money(row.receive),
              title: `kupujący płaci ${money(row.buyer)} · za ${row.sell} szt. dostaniesz ${money(row.receive * row.sell)}`,
            })
          : h('span', { class: 'val none', text: row.priced ? 'za tanio' : 'brak ceny' }),
      );
      item.addEventListener('click', e => {
        if (e.target === cb || e.target === qty || cb.disabled) return;
        cb.checked = !cb.checked; row.checked = cb.checked; render();
      });
      els.list.append(item);
    }

    // podsumowanie + przyciski
    const sel = selected();
    const pieces = sel.reduce((s, r) => s + r.sell, 0);
    const sum = sel.reduce((s, r) => s + r.receive * r.sell, 0);
    els.count.textContent = `${sel.length} poz. / ${pieces} szt.`;
    els.sum.textContent = pieces ? money(sum) : '—';
    els.scan.disabled = state.busy;
    els.dry.disabled = state.busy || !pieces;
    els.sell.disabled = state.busy || !pieces;
    els.selAll.style.display = els.selNone.style.display = state.rows.length ? '' : 'none';
  }

  function askConfirm() {
    // potwierdzenie w panelu zamiast systemowego confirm()
    const sel = selected();
    const pieces = sel.reduce((s, r) => s + r.sell, 0);
    const sum = sel.reduce((s, r) => s + r.receive * r.sell, 0);
    const box = h('div', { class: 'confirm' },
      h('p', { text: `Wystawić ${pieces} szt. (${sel.length} pozycji) za ~${money(sum)}? Każdą ofertę i tak potwierdzasz w apce Steam Mobile.` }),
      h('div', { class: 'row' },
        h('button', { class: 'btn', text: 'Anuluj', onclick: () => box.replaceWith(els.actions) }),
        h('button', {
          class: 'btn good', text: 'Tak, wystaw',
          onclick: () => { box.replaceWith(els.actions); sellSelected(); },
        })),
    );
    els.actions.replaceWith(box);
  }

  if (document.body) buildPanel();
})();
