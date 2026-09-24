(() => {
  const SOURCE_SYSTEM = Object.freeze({ CLASSIC: 'UYAP', KURUM: 'UYAP_KURUM' });
  const KURUM_ADAPTER_VERSION = 'uyap-kurum-adaptive-v14';
  const KURUM_ROW_SELECTORS = Object.freeze([
    '[data-evrak-id]', '[data-document-id]', '[evrak_id]', '[data-row-key]', '[data-uid]', '[row-id]',
    'tr.x-grid-row', '[role="row"]', '.ag-row', '.dx-data-row',
    '.ui-datatable-data > tr', '.mat-row', 'mat-row', '.mat-mdc-row', '.k-table-row', '.k-master-row',
    '.ant-table-row', '.el-table__row', '.dataTables_wrapper tbody tr', 'tbody > tr'
  ]);
  const KURUM_ACTION_SELECTORS = Object.freeze([
    '[data-action*="indir" i]', '[data-action*="download" i]', '[data-action*="kaydet" i]',
    '[data-command*="indir" i]', '[data-command*="download" i]', '[data-command*="kaydet" i]',
    'button[title*="İndir" i]', 'a[title*="İndir" i]', '[title*="Kaydet" i]',
    'button[aria-label*="İndir" i]', 'a[aria-label*="İndir" i]', '[aria-label*="Kaydet" i]',
    'button[class*="download" i]', 'a[class*="download" i]',
    'button[class*="indir" i]', 'a[class*="indir" i]',
    '[onclick*="indir" i]', '[onclick*="download" i]', '[onclick*="kaydet" i]',
    'a[href*="download" i]', 'a[href*="indir" i]', 'a[href*="evrak" i]',
    '[role="button"]', 'button', 'a'
  ]);
  const KURUM_NEXT_SELECTORS = Object.freeze([
    '[aria-label*="Sonraki" i]', '[aria-label*="Next" i]',
    'button[title*="Sonraki" i]', 'a[title*="Sonraki" i]',
    '.ui-paginator-next', '.dx-navigate-button.dx-next-button', '.x-tbar-page-next',
    '.mat-mdc-paginator-navigation-next', '.mat-paginator-navigation-next', '.k-pager-next', '.paginate_button.next',
    '.ant-pagination-next', '.el-pagination .btn-next', 'button[data-page="next"]', 'a[data-page="next"]'
  ]);
  const KURUM_PREV_SELECTORS = Object.freeze([
    '[aria-label*="Önceki" i]', '[aria-label*="Previous" i]', '[aria-label*="Prev" i]',
    'button[title*="Önceki" i]', 'a[title*="Önceki" i]',
    '.ui-paginator-prev', '.dx-navigate-button.dx-prev-button', '.x-tbar-page-prev',
    '.mat-mdc-paginator-navigation-previous', '.mat-paginator-navigation-previous', '.k-pager-prev', '.paginate_button.previous',
    '.ant-pagination-prev', '.el-pagination .btn-prev', 'button[data-page="prev"]', 'a[data-page="prev"]'
  ]);
  const KURUM_VIRTUAL_SELECTORS = Object.freeze([
    '.ag-body-viewport', '.x-grid-scroller', '.dx-scrollable-container',
    '.ui-datatable-scrollable-body', '.cdk-virtual-scroll-viewport', 'cdk-virtual-scroll-viewport', '.k-virtual-content',
    '.rc-virtual-list-holder', '.ant-table-body', '[data-virtual-scroll]', '[data-virtualized]'
  ]);
  const KURUM_SELECTION_SELECTORS = Object.freeze([
    'input[type="checkbox"]', 'input[type="radio"]', '[role="checkbox"]', '[role="radio"]',
    '.ag-selection-checkbox', '.x-grid-row-checker', '.dx-select-checkbox', '.ui-chkbox-box',
    '.select-checkbox', '[class*="select" i][class*="checkbox" i]'
  ]);
  const ID_ATTRIBUTES = Object.freeze([
    'data-document-id', 'data-evrak-id', 'evrak_id', 'document_id',
    'data-id', 'evrakid', 'documentid', 'data-key', 'data-row-key', 'data-uid', 'row-id'
  ]);
  const KURUM_IDENTITY_SELECTORS = Object.freeze([
    '[data-dosya-no]', '[data-dosya-numarasi]', '[data-case-id]', '[data-esas-no]',
    '[data-file-no]', '[data-file-id]',
    'input[name*="dosya" i]', 'input[id*="dosya" i]',
    'input[name*="esas" i]', 'input[id*="esas" i]',
    '[name*="dosyaNo" i]', '[id*="dosyaNo" i]',
    '[name*="esasNo" i]', '[id*="esasNo" i]'
  ]);

  const normalizedText = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const lower = (value) => normalizedText(value).toLocaleLowerCase('tr-TR');
  const safeAttr = (element, name) => normalizedText(element?.getAttribute?.(name));
  const attrBundle = (element) => [
    safeAttr(element, 'title'), safeAttr(element, 'aria-label'), safeAttr(element, 'data-action'),
    safeAttr(element, 'data-command'), safeAttr(element, 'data-original-title'), safeAttr(element, 'data-tooltip'),
    safeAttr(element, 'alt'), safeAttr(element, 'class'), safeAttr(element, 'href'),
    safeAttr(element, 'onclick'), normalizedText(element?.textContent)
  ].filter(Boolean).join(' ');

  function shortScopeHash(value) {
    let a = 0x811c9dc5;
    let b = 0x9e3779b9;
    for (const char of String(value || '')) {
      const code = char.codePointAt(0) || 0;
      a ^= code; a = Math.imul(a, 0x01000193);
      b ^= (code + 0x7f4a7c15) >>> 0; b = Math.imul(b, 0x85ebca6b);
    }
    return `${(a >>> 0).toString(16).padStart(8,'0')}${(b >>> 0).toString(16).padStart(8,'0')}`;
  }

  function elementIdentityText(element) {
    if (!element) return '';
    return [
      safeAttr(element, 'value'), safeAttr(element, 'data-dosya-no'),
      safeAttr(element, 'data-dosya-numarasi'), safeAttr(element, 'data-case-id'),
      safeAttr(element, 'data-esas-no'), safeAttr(element, 'data-file-no'),
      safeAttr(element, 'data-file-id'), normalizedText(element.value), normalizedText(element.textContent)
    ].filter(Boolean).join(' ');
  }

  function cleanUrl(locationObject) {
    try { return `${locationObject.origin}${locationObject.pathname}`; }
    catch { return ''; }
  }

  function diagnosticUrl(locationObject) {
    try {
      const segments = String(locationObject?.pathname || '/').split('/').map((segment) => {
        if (!segment) return '';
        if (/\d/.test(segment) || segment.length > 64) return '<id>';
        return segment.slice(0, 64);
      });
      return `${locationObject.origin}${segments.join('/') || '/'}`;
    } catch { return ''; }
  }

  function queryAll(root, selectors) {
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
      let matches = [];
      try { matches = [...root.querySelectorAll(selector)]; } catch { continue; }
      for (const element of matches) {
        if (seen.has(element)) continue;
        seen.add(element); result.push(element);
      }
    }
    return result;
  }

  function isDataTableExportAction(element) {
    if (!element) return false;
    const classes = lower(safeAttr(element, 'class'));
    const text = lower(normalizedText(element?.textContent));
    const title = lower(`${safeAttr(element, 'title')} ${safeAttr(element, 'aria-label')}`);
    const exportClass = /(?:^|\s)(?:buttons-(?:copy|excel|csv|pdf|print)|dt-button)(?:\s|$)/.test(classes);
    const exportText = /^(?:kopyala|copy|excel|csv|pdf|yazdır|yazdir|print)$/i.test(text || title);
    let exportToolbar = false;
    try { exportToolbar = Boolean(element?.closest?.('.dt-buttons, .dataTables_wrapper .dt-buttons, [class*="buttons-collection"]')); } catch {}
    return Boolean(exportToolbar && exportText) || /buttons-(?:copy|excel|csv|pdf|print)/.test(classes) || (exportClass && exportText);
  }

  function looksLikeDownloadAction(element) {
    if (isDataTableExportAction(element)) return false;
    const icon = element?.querySelector?.('[class*="download" i], [class*="indir" i], [data-icon*="download" i], [data-icon*="save" i], [aria-label*="İndir" i], [title*="İndir" i]');
    const haystack = lower(`${attrBundle(element)} ${attrBundle(icon)}`);
    if (!haystack) return false;
    return /(indir|download|kaydet|save|dosya\s*indir|evrak\s*indir|belge\s*indir)/i.test(haystack)
      && !/(sil|delete|kaldır|iptal|cancel)/i.test(haystack);
  }

  function isDisabled(element) {
    if (!element) return true;
    const disabledAttr = lower(safeAttr(element, 'disabled'));
    const ariaDisabled = lower(safeAttr(element, 'aria-disabled'));
    const classes = lower(safeAttr(element, 'class'));
    return element.disabled === true || disabledAttr === 'disabled' || ariaDisabled === 'true' ||
      /(?:^|\s)(?:disabled|ui-state-disabled|dx-state-disabled|x-item-disabled)(?:\s|$)/.test(classes);
  }

  function detect(documentObject, locationObject) {
    const hostname = lower(locationObject?.hostname || '');
    const pathname = lower(locationObject?.pathname || '');
    const title = lower(documentObject?.title || '');
    const classicTree = Boolean(documentObject?.querySelector?.('ul.treeview span.file[evrak_id], span.file[evrak_id]'));
    const gridHints = queryAll(documentObject, [
      '.ag-root', '.ag-root-wrapper', '.x-grid', '.x-grid-view', '.dx-datagrid',
      '.ui-datatable', '.mat-table', 'mat-table', '.k-grid', '.dataTables_wrapper', '.ant-table', '.el-table', '[role="grid"]', 'table'
    ]).length;
    const rowHints = queryAll(documentObject, KURUM_ROW_SELECTORS).length;
    const downloadHints = queryAll(documentObject, KURUM_ACTION_SELECTORS).filter(looksLikeDownloadAction).length;
    const body = lower(documentObject?.body?.innerText || documentObject?.body?.textContent || '').slice(0, 16000);
    const urlKurum = hostname.includes('kurum') || /(?:^|\/)(?:kurum|kurumportal|kurum-portali)(?:\/|$)/i.test(pathname);
    const textKurum = /\buyap\s*kurum\b/.test(title) || /\buyap\s*kurum\b/.test(body) || /\bkurum\s+portalı\b/.test(body) || /\bkurum\s+işlemleri\b/.test(body);
    const structuralKurum = rowHints > 0 && downloadHints > 0 && gridHints > 0 && (!classicTree || urlKurum || textKurum);
    const isKurum = urlKurum || textKurum || structuralKurum;
    return {
      sourceSystem: isKurum ? SOURCE_SYSTEM.KURUM : SOURCE_SYSTEM.CLASSIC,
      adapter: isKurum ? KURUM_ADAPTER_VERSION : 'uyap-classic-tree-v3',
      confidence: (urlKurum || textKurum) ? 'high' : structuralKurum ? 'structural-high' : classicTree ? 'high' : 'medium',
      cleanPageUrl: cleanUrl(locationObject),
      detectionEvidence: {urlKurum, textKurum, structuralKurum, classicTree, gridHints, rowHints, downloadHints}
    };
  }

  function parseCaseId(text, locationObject, documentObject = null) {
    const candidates = [normalizedText(text)];
    if (documentObject) {
      for (const element of queryAll(documentObject, KURUM_IDENTITY_SELECTORS)) {
        const candidate = elementIdentityText(element);
        if (candidate) candidates.push(candidate);
      }
    }
    const labelled = /(?:dosya|esas|iş|is|soruşturma|sorusturma|takip)\s*(?:no|numarası|numarasi|nolu|numara)?\s*[:#-]?\s*(20\d{2})\s*[\/-]\s*(\d{1,10})/i;
    const generic = /\b(20\d{2})\s*[\/-]\s*(\d{1,10})\b/;
    for (const value of candidates) {
      const match = value.match(labelled) || value.match(generic);
      if (match) return `${match[1]}/${match[2]}`;
    }
    try {
      const url = new URL(locationObject?.href || `${locationObject?.origin || ''}${locationObject?.pathname || ''}`);
      for (const [key, value] of url.searchParams.entries()) {
        if (!/(dosya|esas|case|file|takip|sorusturma|soruşturma)/i.test(key)) continue;
        const match = String(value).match(generic);
        if (match) return `${match[1]}/${match[2]}`;
      }
      const pathMatch = decodeURIComponent(url.pathname || '').match(generic);
      if (pathMatch) return `${pathMatch[1]}/${pathMatch[2]}`;
    } catch {}
    return 'Dosya_Bilinmiyor';
  }

  function institutionFromText(text) {
    const value = normalizedText(text);
    const labeled = value.match(/(?:Kurum|Birim|Mahkeme|Savcılık|Daire|Müdürlük|Başkanlık)\s*(?:Adı)?\s*:\s*([^|•\n]{3,160})/i)?.[1];
    if (labeled) return normalizedText(labeled);
    const court = value.match(/([A-ZÇĞİÖŞÜ0-9][^|•\n]{2,120}?(?:Mahkemesi|Savcılığı|Müdürlüğü|Başkanlığı|Dairesi|Kurumu))/i)?.[1];
    return normalizedText(court);
  }

  function identityElementVisible(element) {
    if (!element) return false;
    if (element.hidden || element.closest?.('[hidden], [aria-hidden="true"]')) return false;
    try {
      const style = globalThis.getComputedStyle?.(element);
      if (style && (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0)) return false;
    } catch {}
    try {
      const rect = element.getBoundingClientRect?.();
      if (rect && rect.width > 0 && rect.height > 0) return true;
      if (typeof element.getClientRects === 'function' && element.getClientRects().length > 0) return true;
    } catch {}
    // Test/synthetic DOMs may not implement layout. In that case, only accept
    // elements explicitly marked active by Bootstrap/ARIA state.
    const modal = element.closest?.('.modal');
    return Boolean(modal?.classList?.contains('in') || modal?.classList?.contains('show') || modal?.getAttribute?.('aria-hidden') === 'false');
  }

  function kurumActiveCaseModal(documentObject) {
    const candidates = queryAll(documentObject, ['.modal-content', '#modal-content']);
    const parsed = candidates.map((element) => {
      const text = normalizedText(element?.textContent || '').slice(0, 1200);
      const match = text.match(/^\s*(20\d{2})\s*[\/-]\s*(\d{1,10})\s*-\s*([^-]{3,160}?)\s*-\s*[^|•\n]{1,160}?(?:Dosyası|Dosyasi)(?=\s|$|[|•])/i);
      return match ? {element, text, match, visible:identityElementVisible(element)} : null;
    }).filter(Boolean);
    const visible = parsed.filter((item) => item.visible);
    if (visible.length === 1) return {...visible[0], candidateCount:parsed.length, visibleCandidateCount:1};
    if (visible.length > 1) {
      const active = visible.filter(({element}) => {
        const modal = element.closest?.('.modal');
        return Boolean(modal?.classList?.contains('in') || modal?.classList?.contains('show') || modal?.getAttribute?.('aria-hidden') === 'false');
      });
      if (active.length === 1) return {...active[0], candidateCount:parsed.length, visibleCandidateCount:visible.length};
      // Multiple visible case modals are unsafe. Do not silently pick the first
      // stale DOM node; fall through to non-modal evidence instead.
      return {element:null, text:'', match:null, ambiguous:true, candidateCount:parsed.length, visibleCandidateCount:visible.length};
    }
    // A case modal can briefly exist before layout settles. Only use a hidden
    // candidate when it is the sole case-bearing modal and its parent is not
    // explicitly hidden. This preserves initial AJAX transitions without letting
    // an old hidden #modal-content override the actually open case.
    if (parsed.length === 1) {
      const only = parsed[0];
      const hiddenByAncestor = Boolean(only.element?.closest?.('[hidden], [aria-hidden="true"]'));
      if (!hiddenByAncestor) return {...only, candidateCount:1, visibleCandidateCount:0, transitional:true};
    }
    return {element:null, text:'', match:null, candidateCount:parsed.length, visibleCandidateCount:0};
  }

  function kurumIdentity(documentObject, locationObject) {
    const bodyText = normalizedText(documentObject?.body?.innerText || documentObject?.body?.textContent || '').slice(0, 20000);
    const headingText = queryAll(documentObject, ['[data-institution-name]', '.kurum-adi', '.birim-adi', '.page-title', 'h1', 'h2', 'h3'])
      .map((element) => normalizedText(element.textContent)).filter(Boolean).join(' | ');

    // UYAP Kurum leaves old modal DOM nodes behind while another case is open.
    // A blind querySelector('#modal-content') can therefore return the previous
    // case (e.g. 2026/221) while the visible modal is 2026/111. Always resolve the
    // single visible case-bearing modal first; never let a hidden stale modal win.
    const activeModal = kurumActiveCaseModal(documentObject);
    const modalMatch = activeModal?.match || null;
    const modalCaseId = modalMatch ? `${modalMatch[1]}/${modalMatch[2]}` : null;
    const modalInstitution = modalMatch ? normalizedText(modalMatch[3]) : '';

    const parsedCaseId = parseCaseId(`${headingText} ${bodyText}`, locationObject, documentObject);
    const explicitCaseId = modalCaseId || parsedCaseId;
    let institutionName = modalInstitution || institutionFromText(`${headingText} ${bodyText}`) || 'UYAP Kurum';
    if (institutionName.length > 160 || /\b(?:Anasayfa|Bilgilerim|Dosya İşlemleri|Yargıtay|Danıştay|İcra Dairesi)\b/i.test(institutionName)) institutionName = modalInstitution || 'UYAP Kurum';
    const scopeBasis = `${cleanUrl(locationObject)}|${institutionName}|${normalizedText(documentObject?.title).slice(0,160)}`;
    const fallbackCaseId = `Kurum_Arsiv_${shortScopeHash(scopeBasis).slice(0,12)}`;
    const caseId = explicitCaseId === 'Dosya_Bilinmiyor' ? fallbackCaseId : explicitCaseId;
    const caseIdentityMode = modalCaseId ? 'active-modal-case-id' : (explicitCaseId === 'Dosya_Bilinmiyor' ? 'page-scope-fallback' : 'explicit-case-id');
    return {
      caseId,
      explicitCaseId: explicitCaseId === 'Dosya_Bilinmiyor' ? null : explicitCaseId,
      caseIdentityMode,
      institutionName,
      courtName: institutionName,
      sourceContext: {
        adapter:KURUM_ADAPTER_VERSION,
        pageUrl:cleanUrl(locationObject),
        institutionName,
        caseId,
        caseIdentityMode,
        modalCaseCandidateCount:Number(activeModal?.candidateCount || 0),
        visibleModalCaseCandidateCount:Number(activeModal?.visibleCandidateCount || 0),
        modalCaseAmbiguous:activeModal?.ambiguous === true,
        modalCaseTransitional:activeModal?.transitional === true
      }
    };
  }

  function actionForRow(row) {
    if (looksLikeDownloadAction(row)) return row;
    const candidates = queryAll(row, KURUM_ACTION_SELECTORS);
    return candidates.find(looksLikeDownloadAction) || null;
  }

  function rowCells(row) {
    return queryAll(row, ['td', '[role="gridcell"]', '.x-grid-cell', '.ag-cell', '.dx-datagrid-text-content']);
  }

  function rowSelectionControl(row) {
    return row ? (queryAll(row, KURUM_SELECTION_SELECTORS)[0] || null) : null;
  }

  function hasStructuredKurumGrid(documentObject) {
    return queryAll(documentObject, [
      '.dataTables_wrapper', '[role="grid"]', '.ui-datatable', '.ag-root', '.x-grid', '.dx-datagrid',
      '.mat-table', 'mat-table', '.k-grid', '.ant-table', '.el-table'
    ]).length > 0;
  }

  const KURUM_CASE_OPEN_SELECTORS = Object.freeze([
    'button', 'a', '[role="button"]', 'input[type="button"]', 'input[type="submit"]'
  ]);

  function tableHeaderTexts(documentObject) {
    return queryAll(documentObject, [
      'thead th', '[role="columnheader"]', '.dataTables_scrollHead th', '.ui-datatable thead th',
      '.ag-header-cell-text', '.x-column-header-text', '.dx-header-row td', '.k-table-th', '.ant-table-thead th'
    ]).map((element) => normalizedText(element?.textContent || element?.value)).filter(Boolean).slice(0, 40);
  }

  function looksLikeCaseOpenAction(element) {
    if (!element) return false;
    // Live UYAP Kurum uses both semantic <button> text and legacy
    // <input type="button" value="Dosya Görüntüle"> controls.
    // textContent is empty for the latter, so value must be part of the
    // evidence or the whole case row is incorrectly discarded.
    const haystack = lower(`${attrBundle(element)} ${safeAttr(element, 'value')} ${normalizedText(element?.value)}`);
    return /(?:dosya\s*(?:görüntüle|goruntule)|dosyayı\s*(?:görüntüle|goruntule)|dosyayi\s*goruntule)/i.test(haystack)
      && !/(indir|download|kaydet|save)/i.test(haystack);
  }

  function caseOpenActionForRow(row) {
    return row ? (queryAll(row, KURUM_CASE_OPEN_SELECTORS).find(looksLikeCaseOpenAction) || null) : null;
  }

  function rowCaseId(row) {
    const text = normalizedText(row?.textContent || '');
    const match = text.match(/\b(20\d{2})\s*[\/-]\s*(\d{1,10})\b/);
    return match ? `${match[1]}/${match[2]}` : null;
  }

  function kurumPortalSignals(documentObject, locationObject = null) {
    const bodyText = lower(documentObject?.body?.innerText || documentObject?.body?.textContent || '').slice(0, 18000);
    const pathname = lower(locationObject?.pathname || '');
    const rootLikePath = pathname === '/' || pathname === '' || /\/main\/jsp\/kurum\/(?:index\.jsp)?$/i.test(pathname);
    const portalWords = [
      /\buyap\s*kurum\b/i,
      /\banasayfa\b/i,
      /\bdosya\s+işlemleri\b/i,
      /\bdosya\s+raporlama\b/i,
      /\bsafahat\s+sorgu\b/i,
      /\bevrak\s+doğrulama\b/i
    ].filter((pattern) => pattern.test(bodyText)).length;
    const frameCount = queryAll(documentObject, ['iframe', 'frame']).length;
    return {rootLikePath, portalWords, frameCount, bodyText};
  }

  function looksLikeKurumCaseListAction(element) {
    if (!element) return false;
    const text = lower(normalizedText(element?.textContent || element?.value));
    const attrs = lower(attrBundle(element));
    const exactText = text === 'dosya raporlama' || text === 'dosya raporlaması' || text === 'dosya raporlamasi';
    const labelled = /(?:^|\s)dosya\s+raporlam(?:a|ası|asi)(?:\s|$)/i.test(`${text} ${attrs}`);
    return Boolean(exactText || labelled) && !/(excel|pdf|csv|kopyala|copy|indir|download)/i.test(`${text} ${attrs}`);
  }

  function kurumCaseListActions(documentObject, visible) {
    const filterActions = (candidates) => candidates.filter((element) => {
      if (!looksLikeKurumCaseListAction(element)) return false;
      if (typeof visible === 'function' && !visible(element)) return false;
      return !isDisabled(element);
    });
    // Prefer actual links/buttons. Older Kurum skins sometimes put onclick on a
    // wrapping LI as well as an inner anchor; counting both would create a false
    // ambiguity. Use onclick containers only when no semantic clickable exists.
    const semantic = filterActions(queryAll(documentObject, ['a', 'button', '[role="button"]']));
    if (semantic.length) return semantic;
    return filterActions(queryAll(documentObject, ['[onclick]']));
  }

  function kurumCaseListAction(documentObject, visible) {
    const actions = kurumCaseListActions(documentObject, visible);
    return actions.length === 1 ? actions[0] : null;
  }

  function kurumViewKind(documentObject, locationObject = null) {
    const headers = tableHeaderTexts(documentObject).map(lower);
    const hasHeader = (...needles) => headers.some((header) => needles.some((needle) => header.includes(needle)));
    const caseSignals = [
      hasHeader('dosya no'),
      hasHeader('dosya türü','dosya turu'),
      hasHeader('dosya durumu'),
      hasHeader('dosya açılış tarihi','dosya acilis tarihi'),
      hasHeader('dosya görüntüle','dosya goruntule')
    ].filter(Boolean).length;
    const documentSignals = [
      hasHeader('evrak'), hasHeader('belge'), hasHeader('doküman','dokuman'), hasHeader('indir','kaydet')
    ].filter(Boolean).length;
    const caseOpenActions = queryAll(documentObject, KURUM_CASE_OPEN_SELECTORS).filter(looksLikeCaseOpenAction).length;
    if ((caseSignals >= 2 && caseOpenActions > 0) || caseSignals >= 3) return 'case-list';
    if (documentSignals >= 2) return 'document-list';
    const structuredRows = queryAll(documentObject, KURUM_ROW_SELECTORS).filter(looksLikeStructuredDataRow);
    const downloadActions = queryAll(documentObject, KURUM_ACTION_SELECTORS).filter(looksLikeDownloadAction);
    if (structuredRows.length > 0 && downloadActions.length > 0 && hasStructuredKurumGrid(documentObject)) return 'document-list';
    if (documentObject?.querySelector?.('ul.treeview span.file[evrak_id], span.file[evrak_id]')) return 'classic-tree';
    const portal = kurumPortalSignals(documentObject, locationObject);
    if ((portal.rootLikePath && portal.portalWords >= 1) || portal.portalWords >= 2 || portal.frameCount > 0) return 'portal-shell';
    return 'unknown';
  }

  function kurumCaseRows(documentObject, visible) {
    if (kurumViewKind(documentObject) !== 'case-list') return [];
    const rows = queryAll(documentObject, KURUM_ROW_SELECTORS);
    return rows.filter((row) => {
      if (typeof visible === 'function' && !visible(row)) return false;
      return Boolean(rowCaseId(row) && caseOpenActionForRow(row));
    });
  }

  function kurumCaseDescriptor(row) {
    const cells = rowCells(row).map((cell) => normalizedText(cell?.textContent)).filter(Boolean);
    const caseId = rowCaseId(row);
    const courtName = (cells[0] || normalizedText(row?.textContent || '').split(caseId || '')[0] || 'UYAP Kurum').slice(0,160);
    const caseType = (cells[2] || '').slice(0,120);
    const caseStatus = (cells[3] || '').slice(0,80);
    return {caseId, courtName, caseType, caseStatus, label:[courtName,caseId,caseType,caseStatus].filter(Boolean).join(' • ')};
  }

  function kurumCaseTargets(documentObject, visible) {
    return kurumCaseRows(documentObject, visible).map((row) => ({
      ...kurumCaseDescriptor(row), row, action:caseOpenActionForRow(row)
    }));
  }

  function kurumCaseTarget(documentObject, visible, targetCaseId) {
    const wanted = normalizedText(targetCaseId).replace(/\s+/g,'');
    const targets = kurumCaseTargets(documentObject, visible);
    const matches = wanted ? targets.filter((item) => String(item.caseId || '').replace(/\s+/g,'') === wanted) : [];
    return {target:matches.length === 1 ? matches[0] : null, matchCount:matches.length, candidateCount:targets.length, targets};
  }

  function rowEvidence(row) {
    const cells = rowCells(row).map((cell) => normalizedText(cell?.textContent)).filter(Boolean).slice(0, 24);
    const attrs = {};
    for (const name of [...ID_ATTRIBUTES, 'data-title', 'data-evrak-adi', 'data-document-name', 'data-folder', 'data-category', 'data-folder-path']) {
      const value = safeAttr(row, name);
      if (value) attrs[name] = value.slice(0, 240);
    }
    return JSON.stringify({cells, attrs, text:normalizedText(row?.textContent).slice(0, 1200)});
  }

  function looksLikeStructuredDataRow(row) {
    if (!row) return false;
    const text = normalizedText(row.textContent);
    if (text.length < 3) return false;
    if (/^(?:kayıt bulunamadı|kayit bulunamadi|sonuç bulunamadı|sonuc bulunamadi|no data|loading|yükleniyor|yukleniyor)$/i.test(text)) return false;
    const cells = rowCells(row);
    if (cells.length >= 2) return true;
    if (rowSelectionControl(row)) return true;
    return Boolean(safeAttr(row, 'data-row-key') || safeAttr(row, 'data-uid') || safeAttr(row, 'row-id'));
  }

  function globalDownloadActions(documentObject, visible, {allowDisabled = false} = {}) {
    return queryAll(documentObject, KURUM_ACTION_SELECTORS).filter((element) => {
      if (!looksLikeDownloadAction(element)) return false;
      if (!allowDisabled && isDisabled(element)) return false;
      if (typeof visible === 'function' && !visible(element)) return false;
      return !element?.closest?.('[data-evrak-id], [data-document-id], [evrak_id], [data-row-key], tr, [role="row"], .ag-row, .x-grid-row, .dx-data-row');
    });
  }

  function globalDownloadAction(documentObject, visible, options = {}) {
    const actions = globalDownloadActions(documentObject, visible, options);
    return actions.length === 1 ? actions[0] : null;
  }

  function sourceToken(row, action) {
    const descendants = row ? queryAll(row, [
      '[data-evrak-id]', '[data-document-id]', '[evrak_id]', '[data-row-key]',
      'input[name*="evrak" i]', 'input[name*="document" i]', 'input[name*="belge" i]',
      'input[id*="evrak" i]', 'input[id*="document" i]', 'input[id*="belge" i]'
    ]).slice(0, 20) : [];
    for (const element of [row, action, ...descendants]) {
      if (!element) continue;
      for (const name of [...ID_ATTRIBUTES, 'value']) {
        const value = safeAttr(element, name) || (name === 'value' ? normalizedText(element?.value) : '');
        if (value && !/^(?:true|false|on|off)$/i.test(value)) return {value, from:name};
      }
      for (const name of ['href', 'onclick']) {
        const value = safeAttr(element, name);
        if (!value) continue;
        const match = value.match(/(?:evrak(?:Id|_id)?|document(?:Id|_id)?|belge(?:Id|_id)?|id)\s*[=:('"\s]+([A-Za-z0-9_-]{4,160})/i);
        if (match) return {value:match[1], from:name};
      }
    }
    return {value:'', from:''};
  }

  function numericSourceDocumentId(row, action) {
    const token = sourceToken(row, action).value;
    const tokenNumeric = String(token || '').match(/^\d{3,}$/)?.[0];
    if (tokenNumeric) return tokenNumeric;
    const values = [];
    for (const element of [row, action]) {
      if (!element) continue;
      for (const name of ID_ATTRIBUTES) values.push(safeAttr(element, name));
      values.push(safeAttr(element, 'href'), safeAttr(element, 'onclick'));
    }
    for (const value of values.filter(Boolean)) {
      const direct = String(value).match(/^\d{3,}$/)?.[0];
      if (direct) return direct;
      const embedded = String(value).match(/(?:evrak(?:Id|_id)?|document(?:Id|_id)?|belge(?:Id|_id)?)[^0-9]{0,8}(\d{3,})/i)?.[1];
      if (embedded) return embedded;
    }
    return null;
  }

  function parseDate(text) {
    const match = String(text || '').match(/\b(\d{2})[./-](\d{2})[./-](\d{4})\b/);
    return match ? `${match[3]}-${match[2]}-${match[1]}` : 'Tarih_Bilinmiyor';
  }

  function titleForRow(row) {
    const explicit = [
      safeAttr(row, 'data-title'), safeAttr(row, 'data-evrak-adi'), safeAttr(row, 'data-document-name'),
      normalizedText(row?.querySelector?.('[data-field="evrakAdi"], .evrak-adi, .document-name, .belge-adi')?.textContent)
    ].find(Boolean);
    if (explicit) return explicit.slice(0, 240);
    const cells = [...(row?.querySelectorAll?.('td, [role="gridcell"], .x-grid-cell, .ag-cell') || [])]
      .map((cell) => normalizedText(cell.textContent))
      .filter((text) => text && !/^\d+$/.test(text) && !/^\d{2}[./-]\d{2}[./-]\d{4}/.test(text) && !/^(indir|download|kaydet)$/i.test(text));
    const best = cells.sort((a,b) => b.length - a.length)[0];
    return (best || normalizedText(row?.textContent) || 'Adsız Evrak').slice(0, 240);
  }

  function folderForRow(row) {
    return safeAttr(row, 'data-folder-path') || safeAttr(row, 'data-category') || safeAttr(row, 'data-folder') || 'Kurum Evrakları';
  }

  function kurumRows(documentObject, visible) {
    // UYAP Kurum "Dosya Raporlama" / dosya listesi ekranı belge envanteri değildir.
    // Bu ekrandaki her satır bir dava/dosya kaydıdır; evrak olarak sayılması 0/8 gibi
    // sahte oturumlar üretir. Gerçek evrak envanteri ancak dosya görüntülendikten sonra taranır.
    if (kurumViewKind(documentObject) === 'case-list') return [];
    const rows = queryAll(documentObject, KURUM_ROW_SELECTORS);
    const globalAction = globalDownloadAction(documentObject, visible, {allowDisabled:true});
    const globalActions = globalDownloadActions(documentObject, visible, {allowDisabled:true});
    const selectableToolbarMode = !globalAction && globalActions.length > 1 && hasStructuredKurumGrid(documentObject);
    return rows.filter((row) => {
      if (typeof visible === 'function' && !visible(row)) return false;
      const rowAction = actionForRow(row);
      const token = sourceToken(row, rowAction).value;
      if (rowAction) return Boolean(token || normalizedText(row.textContent).length >= 3);
      // A number of Kurum grids use one toolbar download button for the selected row.
      // In that mode only rows with a stable source token/id are considered documents.
      if (globalAction && token) return true;
      // Some current UYAP Kurum DataTables expose multiple disabled global
      // download commands and no source id directly on the row. Discovery is
      // allowed only for a structured grid row. Before any download, the row
      // must be selected and the toolbar must collapse to exactly one enabled
      // download command; otherwise preflight still fails closed.
      return Boolean(selectableToolbarMode && looksLikeStructuredDataRow(row));
    });
  }

  function extractKurumCandidate(row, documentObject = null, visible = null) {
    const rowAction = actionForRow(row);
    const toolbarAction = rowAction ? null : globalDownloadAction(documentObject, visible, {allowDisabled:true});
    const toolbarCandidates = rowAction ? [] : globalDownloadActions(documentObject, visible, {allowDisabled:true});
    const selectableToolbarAction = !toolbarAction && toolbarCandidates.length > 1 && hasStructuredKurumGrid(documentObject) && looksLikeStructuredDataRow(row)
      ? toolbarCandidates[0]
      : null;
    const action = rowAction || toolbarAction || selectableToolbarAction;
    const token = sourceToken(row, rowAction || row);
    const evidence = rowEvidence(row);
    return {
      action,
      actionMode: rowAction ? 'row-action' : toolbarAction ? 'global-toolbar' : selectableToolbarAction ? 'global-toolbar-select' : 'none',
      sourceToken: token.value,
      tokenSource: token.from,
      sourceDocumentId: numericSourceDocumentId(row, rowAction || row),
      documentTitle: titleForRow(row),
      documentDate: parseDate(normalizedText(row?.textContent)),
      folderName: folderForRow(row),
      identityEvidence: JSON.stringify([
        token.from, token.value, titleForRow(row), parseDate(normalizedText(row?.textContent)), folderForRow(row), evidence
      ])
    };
  }

  function kurumPager(documentObject, visible) {
    const nextCandidates = queryAll(documentObject, KURUM_NEXT_SELECTORS).filter((el) => typeof visible !== 'function' || visible(el));
    const prevCandidates = queryAll(documentObject, KURUM_PREV_SELECTORS).filter((el) => typeof visible !== 'function' || visible(el));
    const next = nextCandidates.find((el) => !isDisabled(el)) || nextCandidates[0] || null;
    const prev = prevCandidates.find((el) => !isDisabled(el)) || prevCandidates[0] || null;
    const bodyText = normalizedText(documentObject?.body?.innerText || documentObject?.body?.textContent || '');
    const pageCount = Number(
      bodyText.match(/Toplam\s+(\d+)\s+sayfa/i)?.[1] ||
      bodyText.match(/(\d+)\s*\/\s*(\d+)\s*sayfa/i)?.[2] ||
      safeAttr(documentObject?.querySelector?.('[data-page-count]'), 'data-page-count') || 1
    );
    const currentPage = Number(
      safeAttr(documentObject?.querySelector?.('[aria-current="page"]'), 'data-page') ||
      normalizedText(documentObject?.querySelector?.('[aria-current="page"]')?.textContent) ||
      normalizedText(documentObject?.querySelector?.('.ui-paginator-page.ui-state-active, .dx-page.dx-selection, .x-tbar-page-number')?.textContent) ||
      bodyText.match(/\b(\d+)\s*\/\s*\d+\s*sayfa/i)?.[1] || 1
    );
    return {
      next,
      prev,
      nextEnabled:Boolean(next && !isDisabled(next)),
      prevEnabled:Boolean(prev && !isDisabled(prev)),
      pageCount:Number.isFinite(pageCount) && pageCount > 0 ? pageCount : 1,
      currentPage:Number.isFinite(currentPage) && currentPage > 0 ? currentPage : 1,
      nextSignals:nextCandidates.length,
      prevSignals:prevCandidates.length
    };
  }

  function kurumVirtualViewport(documentObject, visible) {
    const candidates = queryAll(documentObject, KURUM_VIRTUAL_SELECTORS);
    return candidates.find((element) => (typeof visible !== 'function' || visible(element)) && Number(element?.scrollHeight || 0) > Number(element?.clientHeight || 0)) ||
      candidates.find((element) => typeof visible !== 'function' || visible(element)) || null;
  }

  function declaredRowCount(documentObject) {
    const values = queryAll(documentObject, ['[aria-rowcount]', '[data-total-count]', '[data-row-count]'])
      .map((element) => Number(String(safeAttr(element, 'aria-rowcount') || safeAttr(element, 'data-total-count') || safeAttr(element, 'data-row-count') || 0).replace(/[^0-9]/g,'')))
      .filter(Number.isFinite);
    const bodyText = normalizedText(documentObject?.body?.innerText || documentObject?.body?.textContent || '');
    const textual = [
      bodyText.match(/Toplam\s+([0-9][0-9.,]*)\s+(?:kayıt|kayit|evrak|sonuç|sonuc)/i)?.[1],
      bodyText.match(/(?:Gösterilen|Kayıt)\s*[: ]\s*\d+\s*[-–]\s*\d+\s*[/|]\s*([0-9][0-9.,]*)/i)?.[1],
      bodyText.match(/\d+\s*[-–]\s*\d+\s+\/\s+([0-9][0-9.,]*)/)?.[1]
    ].filter(Boolean).map((value) => Number(String(value).replace(/[^0-9]/g,''))).filter(Number.isFinite);
    return Math.max(0, ...values, ...textual);
  }

  function kurumDiagnostics(documentObject, locationObject, visible) {
    const rows = kurumRows(documentObject, visible);
    const allActions = queryAll(documentObject, KURUM_ACTION_SELECTORS).filter(looksLikeDownloadAction);
    const globalActionsIncludingDisabled = globalDownloadActions(documentObject, visible, {allowDisabled:true});
    const globalActionsEnabled = globalDownloadActions(documentObject, visible, {allowDisabled:false});
    const pager = kurumPager(documentObject, visible);
    const declared = declaredRowCount(documentObject);
    const tableRows = queryAll(documentObject, ['tr', '[role="row"]', '.x-grid-row', '.ag-row', '.dx-data-row']);
    const frameworkHints = {
      agGrid: queryAll(documentObject, ['.ag-root', '.ag-root-wrapper', '.ag-body-viewport', '.ag-row']).length > 0,
      extGrid: queryAll(documentObject, ['.x-grid', '.x-grid-view', '.x-grid-row', '.x-grid-scroller']).length > 0,
      devExtreme: queryAll(documentObject, ['.dx-datagrid', '.dx-data-row', '.dx-scrollable-container']).length > 0,
      primeFaces: queryAll(documentObject, ['.ui-datatable', '.ui-datatable-data', '.ui-datatable-scrollable-body']).length > 0,
      angularMaterial: queryAll(documentObject, ['.mat-table', 'mat-table', '.mat-row', '.mat-mdc-row', '.mat-mdc-paginator']).length > 0,
      kendo: queryAll(documentObject, ['.k-grid', '.k-table-row', '.k-pager-wrap', '.k-pager']).length > 0,
      dataTables: queryAll(documentObject, ['.dataTables_wrapper', '.paginate_button', '.dataTables_info']).length > 0,
      antDesign: queryAll(documentObject, ['.ant-table', '.ant-table-row', '.ant-pagination']).length > 0,
      elementPlus: queryAll(documentObject, ['.el-table', '.el-table__row', '.el-pagination']).length > 0,
      ariaGrid: queryAll(documentObject, ['[role="grid"]', '[role="row"]', '[role="gridcell"]']).length > 0,
      htmlTable: queryAll(documentObject, ['table', 'tbody > tr']).length > 0
    };
    const viewport = kurumVirtualViewport(documentObject, visible);
    const sourceTokenRows = rows.filter((row) => Boolean(sourceToken(row, actionForRow(row) || row).value)).length;
    const viewKind = kurumViewKind(documentObject, locationObject);
    const caseRows = kurumCaseRows(documentObject, visible);
    const headers = tableHeaderTexts(documentObject);
    return {
      adapter: KURUM_ADAPTER_VERSION,
      viewKind,
      caseListRowCount: caseRows.length,
      caseListOpenActions: caseRows.filter((row) => Boolean(caseOpenActionForRow(row))).length,
      portalCaseListActions: kurumCaseListActions(documentObject, visible).length,
      portalFrameCount: kurumPortalSignals(documentObject, locationObject).frameCount,
      tableHeaders: headers.slice(0, 12),
      pageUrl: diagnosticUrl(locationObject),
      candidateRows: rows.length,
      candidateDownloadActions: allActions.length,
      globalToolbarDownload: globalActionsIncludingDisabled.length === 1,
      globalToolbarDownloadEnabled: globalActionsEnabled.length === 1,
      globalToolbarDownloadCandidates: globalActionsIncludingDisabled.length,
      sourceTokenRows,
      selectableToolbarRows: rows.filter((row) => extractKurumCandidate(row, documentObject, visible).actionMode === 'global-toolbar-select').length,
      tableRows: tableRows.length,
      pageCount: pager.pageCount,
      currentPage: pager.currentPage,
      declaredRowCount: declared,
      framesHint: Boolean(documentObject?.querySelector?.('iframe')),
      virtualViewportSignals: queryAll(documentObject, KURUM_VIRTUAL_SELECTORS).length,
      virtualViewportScrollable: Boolean(viewport && Number(viewport.scrollHeight || 0) > Number(viewport.clientHeight || 0)),
      nextPageSignals: pager.nextSignals,
      nextPageEnabledSignals: pager.nextEnabled ? 1 : 0,
      prevPageSignals: pager.prevSignals,
      frameworkHints,
      samples: rows.slice(0, 3).map((row) => {
        const candidate = extractKurumCandidate(row, documentObject, visible);
        return {
          hasSourceToken:Boolean(candidate.sourceToken),
          hasNumericSourceId:Boolean(candidate.sourceDocumentId),
          hasDownloadAction:Boolean(candidate.action),
          actionMode:candidate.actionMode,
          hasSelectionControl:Boolean(rowSelectionControl(row)),
          cellCount:rowCells(row).length
        };
      })
    };
  }

  globalThis.UYAP_SOURCE_ADAPTERS = Object.freeze({
    SOURCE_SYSTEM,
    KURUM_ADAPTER_VERSION,
    detect,
    kurumIdentity,
    kurumViewKind,
    kurumPortalSignals,
    kurumCaseListActions,
    kurumCaseListAction,
    kurumCaseRows,
    kurumCaseTargets,
    kurumCaseTarget,
    caseOpenActionForRow,
    rowCaseId,
    kurumRows,
    extractKurumCandidate,
    kurumDiagnostics,
    kurumPager,
    kurumVirtualViewport,
    declaredRowCount,
    globalDownloadAction,
    globalDownloadActions,
    looksLikeDownloadAction,
    actionForRow,
    isDisabled
  });
})();
