(() => {
  const VERSION = '2.9.0';
  const ATTR = 'data-uyap-archive-kurum-case-context';
  const EVENT = 'uyap-archive-kurum-case-context';
  const OPEN_REQUEST_ATTR = 'data-uyap-archive-kurum-open-request';
  const OPEN_RESPONSE_ATTR = 'data-uyap-archive-kurum-open-response';
  const OPEN_REQUEST_EVENT = 'uyap-archive-kurum-open-request';
  const OPEN_RESPONSE_EVENT = 'uyap-archive-kurum-open-response';
  const WRAPPED = Symbol.for('uyap.archive.kurum.baslat.wrapped');
  let lastPublishedOpenContext = null;

  function text(value, max = 180) {
    return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
  }

  function normalize(value) {
    return text(value, 300).toLocaleLowerCase('tr-TR');
  }

  function normalizedCaseId(value) {
    const raw = text(value, 50).replace(/\s+/g, '').replace('-', '/');
    return /^20\d{2}\/\d{1,10}$/.test(raw) ? raw : '';
  }

  function safeOpenContext(data) {
    if (!data || typeof data !== 'object') return null;
    const dosyaNo = normalizedCaseId(data.dosyaNo || data.caseId);
    const dosyaId = text(data.dosyaId, 240);
    if (!dosyaNo || !dosyaId) return null;
    return {
      dosyaId,
      dosyaNo,
      birimId:text(data.birimId, 100),
      birimAdi:text(data.birimAdi || data.courtName || 'UYAP Kurum', 180),
      dosyaTurKod:text(data.dosyaTurKod, 60),
      birimTuru1:text(data.birimTuru1, 80),
      birimTuru2:text(data.birimTuru2, 80),
      birimTuru3:text(data.birimTuru3, 80),
      kurumNo:text(data.kurumNo, 100)
    };
  }

  function publish(data) {
    const caseId = normalizedCaseId(data?.dosyaNo || data?.caseId);
    if (!caseId) return;
    const openContext = safeOpenContext(data);
    if (openContext) lastPublishedOpenContext = openContext;
    const safe = {
      version: VERSION,
      caseId,
      courtName: text(data?.birimAdi || data?.courtName || 'UYAP Kurum'),
      birimId: text(data?.birimId, 80),
      dosyaTurKod: text(data?.dosyaTurKod, 40),
      kurumNo: text(data?.kurumNo, 80),
      openContext,
      capturedAt: new Date().toISOString()
    };
    try {
      document.documentElement?.setAttribute(ATTR, JSON.stringify(safe));
      document.dispatchEvent(new Event(EVENT));
    } catch {}
  }

  function wrap() {
    const host = globalThis.DosyaBilgileriGoruntuleme;
    const original = host?.baslat;
    if (!host || typeof original !== 'function') return false;
    if (original[WRAPPED]) return true;

    function wrapped(data, ...args) {
      publish(data);
      return original.apply(this, [data, ...args]);
    }
    try { Object.defineProperty(wrapped, WRAPPED, {value:true}); } catch {}
    try { host.baslat = wrapped; } catch { return false; }
    return host.baslat === wrapped;
  }

  function response(requestId, payload) {
    try {
      const body = {requestId, ...payload};
      document.documentElement?.setAttribute(OPEN_RESPONSE_ATTR, JSON.stringify(body));
      document.dispatchEvent(new Event(OPEN_RESPONSE_EVENT));
    } catch {}
  }

  function rowCaseId(row) {
    const m = text(row?.textContent, 500).match(/\b(20\d{2})\s*[\/-]\s*(\d{1,10})\b/);
    return m ? `${m[1]}/${m[2]}` : '';
  }

  function rowCourt(row) {
    try {
      const cells = [...row.querySelectorAll('td,[role="gridcell"]')];
      return text(cells[0]?.textContent || '', 180);
    } catch { return ''; }
  }

  function looksLikeOpenAction(element) {
    const hay = normalize(`${element?.textContent || ''} ${element?.value || ''} ${element?.getAttribute?.('title') || ''} ${element?.getAttribute?.('aria-label') || ''}`);
    return /dosya\s*(?:görüntüle|goruntule)/i.test(hay);
  }

  function clickCaseRow(row) {
    const action = [...row.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"]')].find(looksLikeOpenAction);
    if (!action) return false;
    try {
      const jq = globalThis.jQuery || globalThis.$;
      if (jq) { jq(action).trigger('click'); return true; }
    } catch {}
    try { action.click(); return true; } catch { return false; }
  }

  function allDataTableRows() {
    const table = document.querySelector('#dosya_listesi_table');
    if (!table) return [];
    try {
      const jq = globalThis.jQuery || globalThis.$;
      if (jq?.fn?.DataTable && jq.fn.DataTable.isDataTable?.(table)) {
        const dt = jq(table).DataTable();
        const nodes = dt.rows({search:'none'}).nodes()?.toArray?.() || dt.rows().nodes()?.toArray?.() || [];
        if (nodes.length) return nodes;
      }
    } catch {}
    return [...table.querySelectorAll('tbody tr')];
  }

  function findRows(caseId, courtName='') {
    const wantedCase = normalizedCaseId(caseId);
    const wantedCourt = normalize(courtName);
    const rows = allDataTableRows().filter((row) => rowCaseId(row) === wantedCase);
    if (!wantedCourt || /^uyap kurum$/i.test(wantedCourt)) return rows;
    const exact = rows.filter((row) => normalize(rowCourt(row)) === wantedCourt);
    if (exact.length) return exact;
    const loose = rows.filter((row) => {
      const c = normalize(rowCourt(row));
      return c && (c.includes(wantedCourt) || wantedCourt.includes(c));
    });
    return loose.length ? loose : rows;
  }

  function clearDataTableFilters() {
    try {
      const table = document.querySelector('#dosya_listesi_table');
      const jq = globalThis.jQuery || globalThis.$;
      if (!table || !jq?.fn?.DataTable || !jq.fn.DataTable.isDataTable?.(table)) return;
      const dt = jq(table).DataTable();
      dt.search('');
      dt.columns().search('');
      dt.draw();
    } catch {}
  }

  function inferYargiTuru(courtName) {
    const c = normalize(courtName);
    if (/arabulucu/.test(c)) return '25';
    if (/satış|satis/.test(c)) return '11';
    if (/idari|vergi/.test(c)) return '6';
    if (/icra|icra/.test(c)) return '2';
    if (/ceza/.test(c)) return '0';
    if (/hukuk/.test(c)) return '1';
    return '';
  }

  function optionByText(select, wantedText) {
    const wanted = normalize(wantedText);
    if (!select || !wanted) return null;
    const options = [...select.options];
    return options.find((opt) => normalize(opt.textContent) === wanted)
      || options.find((opt) => {
        const t = normalize(opt.textContent);
        return t && (t.includes(wanted) || wanted.includes(t));
      }) || null;
  }

  async function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  async function waitForRow(caseId, courtName, timeoutMs=5000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const rows = findRows(caseId, courtName);
      if (rows.length === 1) return rows[0];
      await wait(120);
    }
    return null;
  }

  async function configureCaseQuery(courtName) {
    const jq = globalThis.jQuery || globalThis.$;

    // The case detail modal does not reset the hidden Dosya Raporlama form.
    // Reuse the current case's institution when available so an old paused job
    // can be located without asking the user to reopen the list manually.
    const kurumSelect = document.querySelector('#kurumListesi');
    if (kurumSelect && lastPublishedOpenContext?.kurumNo) {
      try {
        const current = jq ? jq('#kurumListesi').select2('val') : kurumSelect.value;
        if (!current) {
          if (jq) jq('#kurumListesi').val(lastPublishedOpenContext.kurumNo).trigger('change');
          else { kurumSelect.value = lastPublishedOpenContext.kurumNo; kurumSelect.dispatchEvent(new Event('change',{bubbles:true})); }
          await wait(150);
        }
      } catch {}
    }

    const yargiTuruValue = inferYargiTuru(courtName);
    if (yargiTuruValue && document.querySelector('#yargiTuru')) {
      try {
        if (jq) jq('#yargiTuru').val(yargiTuruValue).trigger('change');
        else {
          const typeSelect = document.querySelector('#yargiTuru');
          typeSelect.value = yargiTuruValue;
          typeSelect.dispatchEvent(new Event('change',{bubbles:true}));
        }
      } catch {}

      // UYAP's own DosyaSorgula source only calls yargiBirimleriYukle("0")
      // during initialization. Programmatic select2 changes therefore may leave
      // the previous court list in place. Explicitly reload it for the target
      // jurisdiction before choosing the court.
      try {
        if (typeof globalThis.yargiBirimleriYukle === 'function') globalThis.yargiBirimleriYukle(yargiTuruValue);
      } catch {}
      await wait(250);
    }

    const unitSelect = document.querySelector('#yargiBirimi');
    if (unitSelect && courtName) {
      let option = optionByText(unitSelect, courtName);
      const deadline = Date.now() + 5000;
      while (!option && Date.now() < deadline) {
        await wait(150);
        option = optionByText(unitSelect, courtName);
      }
      try {
        if (option) {
          if (jq) jq('#yargiBirimi').val(option.value).trigger('change');
          else { unitSelect.value = option.value; unitSelect.dispatchEvent(new Event('change', {bubbles:true})); }
        } else {
          // Never leave the currently open case's court selected when looking
          // for a different paused case. An empty court means query all units
          // for the target jurisdiction, which is slower but correct.
          if (jq) jq('#yargiBirimi').val('').trigger('change');
          else { unitSelect.value = ''; unitSelect.dispatchEvent(new Event('change', {bubbles:true})); }
        }
      } catch {}
      await wait(180);
    }
  }

  async function queryAndOpen(caseId, courtName) {
    clearDataTableFilters();
    let rows = findRows(caseId, courtName);
    if (rows.length === 1 && clickCaseRow(rows[0])) return {ok:true,mode:'datatable-existing'};

    const query = globalThis.DosyaSorgula;
    if (!query || typeof query.dosyalariGetir !== 'function') {
      return {ok:false,error:'DosyaSorgula API bulunamadı; otomatik dosya sorgusu yapılamadı.'};
    }

    await configureCaseQuery(courtName);
    try { query.dosyalariGetir(0); } catch (error) {
      return {ok:false,error:`Dosya sorgusu başlatılamadı: ${String(error?.message || error)}`};
    }

    let row = await waitForRow(caseId, courtName, 6500);
    if (row && clickCaseRow(row)) return {ok:true,mode:'auto-query'};

    // UYAP Kurum server-side listesi 500 kayıtlık sayfalar kullanabilir.
    // Kullanıcıdan filtre temizlemesini istemek yerine ilerleyen sayfaları güvenli
    // biçimde kendimiz dolaşırız. Buton görünmüyorsa veya hedef bulunursa dururuz.
    for (let page = 0; page < 20; page += 1) {
      const next = document.querySelector('#aramaIleri');
      const style = next ? getComputedStyle(next) : null;
      const visible = Boolean(next && style && style.display !== 'none' && style.visibility !== 'hidden');
      if (!visible) break;
      try { query.dosyalariGetir(1); } catch { break; }
      row = await waitForRow(caseId, courtName, 4500);
      if (row && clickCaseRow(row)) return {ok:true,mode:'auto-query-paged',page:page+2};
    }

    // Last fallback: if the exact court/unit filter produced no row, clear
    // the unit filter and query the same jurisdiction once more. This handles
    // portals where the target court option is not hydrated while another case
    // modal is open.
    try {
      const jq = globalThis.jQuery || globalThis.$;
      const unit = document.querySelector('#yargiBirimi');
      if (unit) {
        if (jq) jq('#yargiBirimi').val('').trigger('change');
        else { unit.value=''; unit.dispatchEvent(new Event('change',{bubbles:true})); }
        query.dosyalariGetir(0);
        row = await waitForRow(caseId, courtName, 6500);
        if (row && clickCaseRow(row)) return {ok:true,mode:'auto-query-all-units'};
        for (let page = 0; page < 20; page += 1) {
          const next = document.querySelector('#aramaIleri');
          const style = next ? getComputedStyle(next) : null;
          const nextVisible = Boolean(next && style && style.display !== 'none' && style.visibility !== 'hidden');
          if (!nextVisible) break;
          try { query.dosyalariGetir(1); } catch { break; }
          row = await waitForRow(caseId, courtName, 4500);
          if (row && clickCaseRow(row)) return {ok:true,mode:'auto-query-all-units-paged',page:page+2};
        }
      }
    } catch {}

    return {ok:false,error:`${caseId} dosyası otomatik sorguda bulunamadı.`};
  }

  async function handleOpenRequest() {
    let request = null;
    try { request = JSON.parse(document.documentElement?.getAttribute(OPEN_REQUEST_ATTR) || 'null'); } catch {}
    const requestId = text(request?.requestId, 120);
    if (!requestId) return;
    const caseId = normalizedCaseId(request?.caseId);
    const courtName = text(request?.courtName || 'UYAP Kurum', 180);
    if (!caseId) { response(requestId,{ok:false,error:'Geçerli dosya numarası yok.'}); return; }

    try {
      wrap();
      const direct = safeOpenContext(request?.openContext);
      const host = globalThis.DosyaBilgileriGoruntuleme;
      if (direct && host && typeof host.baslat === 'function') {
        publish(direct);
        host.baslat(direct);
        response(requestId,{ok:true,mode:'saved-context'});
        return;
      }

      const result = await queryAndOpen(caseId,courtName);
      response(requestId,result);
    } catch (error) {
      response(requestId,{ok:false,error:String(error?.message || error)});
    }
  }

  document.addEventListener(OPEN_REQUEST_EVENT, () => { void handleOpenRequest(); });
  wrap();
  const timer = setInterval(wrap, 750);
  addEventListener('pagehide', () => clearInterval(timer), {once:true});
})();
