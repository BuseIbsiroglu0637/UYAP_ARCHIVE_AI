(() => {
  const VERSION = chrome.runtime?.getManifest?.()?.version || '2.9.0';
  if (globalThis.UYAP_ARCHIVE_CONTENT_VERSION === VERSION) return;
  globalThis.UYAP_ARCHIVE_CONTENT_VERSION = VERSION;
  const profile = globalThis.UYAP_ARCHIVE_PROFILE;
  const adapters = globalThis.UYAP_SOURCE_ADAPTERS;
  const MESSAGE = Object.freeze({
    SCAN_PAGE: "scan_page",
    CLICK_DOCUMENT: "click_document",
    PREFLIGHT_DOCUMENT: "preflight_document",
    REBIND_DOCUMENT: "rebind_document",
    PROBE_PAGE: "probe_page",
    SOURCE_DIAGNOSTIC: "source_diagnostic",
    FORMAT_CENSUS: "format_census",
    LIST_KURUM_CASES: "list_kurum_cases",
    OPEN_KURUM_CASE: "open_kurum_case",
    OPEN_KURUM_CASE_SMART: "open_kurum_case_smart",
    OPEN_KURUM_CASE_LIST: "open_kurum_case_list",
    KURUM_CASE_CONTEXT_OBSERVED: "kurum_case_context_observed",
    SESSION_HEARTBEAT: "session_heartbeat",
    SESSION_EXPIRED_SIGNAL: "session_expired_signal"
  });
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const KURUM_CONTEXT_ATTR = 'data-uyap-archive-kurum-case-context';
  const KURUM_CONTEXT_EVENT = 'uyap-archive-kurum-case-context';
  const KURUM_CONTEXT_STORAGE = 'uyapArchiveKurumSelectionV2';
  const KURUM_OPEN_REQUEST_ATTR = 'data-uyap-archive-kurum-open-request';
  const KURUM_OPEN_RESPONSE_ATTR = 'data-uyap-archive-kurum-open-response';
  const KURUM_OPEN_REQUEST_EVENT = 'uyap-archive-kurum-open-request';
  const KURUM_SELECTION_MAX_AGE_MS = 120_000;
  let lastKurumCaseSelection = null;
  let lastPersistedKurumContextSignature = "";

  function sanitizeKurumSelection(value) {
    if (!value || typeof value !== 'object') return null;
    const caseId = String(value.caseId || value.dosyaNo || '').replace(/\s+/g,'').replace('-', '/');
    if (!/^20\d{2}\/\d{1,10}$/.test(caseId)) return null;
    const courtName = normalizedText(value.courtName || value.birimAdi || 'UYAP Kurum').slice(0,160);
    const rawOpen = value.openContext && typeof value.openContext === 'object' ? value.openContext : value;
    const dosyaId = normalizedText(rawOpen.dosyaId || '').slice(0,240);
    const openContext = dosyaId ? {
      dosyaId,
      dosyaNo:caseId,
      birimId:normalizedText(rawOpen.birimId || '').slice(0,100),
      birimAdi:normalizedText(rawOpen.birimAdi || courtName || 'UYAP Kurum').slice(0,180),
      dosyaTurKod:normalizedText(rawOpen.dosyaTurKod || '').slice(0,60),
      birimTuru1:normalizedText(rawOpen.birimTuru1 || '').slice(0,80),
      birimTuru2:normalizedText(rawOpen.birimTuru2 || '').slice(0,80),
      birimTuru3:normalizedText(rawOpen.birimTuru3 || '').slice(0,80),
      kurumNo:normalizedText(rawOpen.kurumNo || '').slice(0,100)
    } : null;
    return {caseId, courtName: courtName || 'UYAP Kurum', openContext, capturedAt:value.capturedAt || new Date().toISOString()};
  }

  function persistObservedKurumSelection(safe) {
    if (!safe?.openContext?.dosyaId) return;
    const open = safe.openContext;
    const signature = [safe.caseId, open.dosyaId, open.birimId, open.kurumNo].join('|');
    if (!signature || signature === lastPersistedKurumContextSignature) return;
    lastPersistedKurumContextSignature = signature;
    try {
      const pending = chrome.runtime.sendMessage({type:MESSAGE.KURUM_CASE_CONTEXT_OBSERVED, selection:safe});
      pending?.catch?.(() => { if (lastPersistedKurumContextSignature === signature) lastPersistedKurumContextSignature = ''; });
    } catch {
      if (lastPersistedKurumContextSignature === signature) lastPersistedKurumContextSignature = '';
    }
  }

  function rememberKurumSelection(value) {
    const safe = sanitizeKurumSelection(value);
    if (!safe) return null;
    lastKurumCaseSelection = safe;
    try { sessionStorage.setItem(KURUM_CONTEXT_STORAGE, JSON.stringify(safe)); } catch {}
    persistObservedKurumSelection(safe);
    return safe;
  }

  function selectionIsFresh(selection) {
    const at = Date.parse(selection?.capturedAt || '');
    return Number.isFinite(at) && (Date.now() - at) >= 0 && (Date.now() - at) <= KURUM_SELECTION_MAX_AGE_MS;
  }

  function readKurumBridgeSelection() {
    const accept = (value) => {
      const safe = sanitizeKurumSelection(value);
      if (!safe || !selectionIsFresh(safe)) return null;
      lastKurumCaseSelection = safe;
      try { sessionStorage.setItem(KURUM_CONTEXT_STORAGE, JSON.stringify(safe)); } catch {}
      return safe;
    };
    try {
      const raw = document.documentElement?.getAttribute(KURUM_CONTEXT_ATTR);
      if (raw) {
        const parsed = accept(JSON.parse(raw));
        if (parsed) { persistObservedKurumSelection(parsed); return parsed; }
      }
    } catch {}
    try {
      const raw = sessionStorage.getItem(KURUM_CONTEXT_STORAGE);
      if (raw) {
        const parsed = accept(JSON.parse(raw));
        if (parsed) { persistObservedKurumSelection(parsed); return parsed; }
        sessionStorage.removeItem(KURUM_CONTEXT_STORAGE);
      }
    } catch {}
    return selectionIsFresh(lastKurumCaseSelection) ? lastKurumCaseSelection : null;
  }

  function effectiveKurumIdentity() {
    const base = adapters.kurumIdentity(document, location);
    const viewKind = adapters.kurumViewKind?.(document, location) || 'unknown';
    const bridgeSelection = viewKind === 'case-list' ? null : readKurumBridgeSelection();

    // The visible Kurum modal is the authoritative identity. A bridge/sessionStorage
    // selection is only a short-lived transition hint while AJAX is opening the modal.
    // When the hint belongs to the same visible case, keep only its safe reopen payload
    // so a paused job can later reopen the case without asking the user to click
    // “Dosya Görüntüle” again.
    if (base?.caseIdentityMode === 'active-modal-case-id' && /^20\d{2}\/\d{1,10}$/.test(normalizedText(base.caseId))) {
      const matchingOpenContext = bridgeSelection?.caseId === base.caseId ? bridgeSelection.openContext || null : null;
      return {
        ...base,
        identitySource:'active-modal-dom',
        sourceContext:{
          ...(base.sourceContext || {}),
          identitySource:'active-modal-dom',
          kurumCaseOpenContext:matchingOpenContext
        }
      };
    }

    const selected = bridgeSelection;
    if (!selected) return {...base, identitySource:base?.caseIdentityMode || 'page-derived'};

    const selectedCourt = normalizedText(selected.courtName);
    const baseCourt = normalizedText(base.institutionName || base.courtName);
    const courtName = (!selectedCourt || /^UYAP Kurum$/i.test(selectedCourt)) && baseCourt && !/^UYAP Kurum$/i.test(baseCourt)
      ? baseCourt
      : (selectedCourt || baseCourt || 'UYAP Kurum');
    return {
      ...base,
      caseId:selected.caseId,
      explicitCaseId:selected.caseId,
      caseIdentityMode:'selected-case-transition-hint',
      identitySource:'bridge-transition-hint',
      institutionName:courtName,
      courtName,
      sourceContext:{
        ...(base.sourceContext || {}),
        caseId:selected.caseId,
        institutionName:courtName,
        caseIdentityMode:'selected-case-transition-hint',
        identitySource:'bridge-transition-hint',
        kurumCaseOpenContext:selected.openContext || null
      }
    };
  }

  document.addEventListener(KURUM_CONTEXT_EVENT, () => { const selected = readKurumBridgeSelection(); persistObservedKurumSelection(selected); });
  persistObservedKurumSelection(readKurumBridgeSelection());

  // Capture manual Kurum case selection too. This runs before the portal's own
  // click handler, so the selected case survives the AJAX transition even when
  // the user opens the case without using the extension popup.
  document.addEventListener('click', (event) => {
    try {
      const target = event.target?.closest?.('button,a,[role="button"],input[type="button"],input[type="submit"]');
      if (!target || adapters?.detect?.(document, location)?.sourceSystem !== adapters?.SOURCE_SYSTEM?.KURUM) return;
      const matches = adapters.kurumCaseTargets?.(document, visible) || [];
      const selected = matches.find((item) => item.action === target || item.action?.contains?.(event.target));
      if (selected) rememberKurumSelection({caseId:selected.caseId,courtName:selected.courtName});
    } catch {}
  }, true);

  function normalizedText(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function visible(element) {
    if (!element?.isConnected) return false;
    let current = element;
    while (current) {
      const style = getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
      current = current.parentElement;
    }
    const rectangle = element.getBoundingClientRect();
    return rectangle.width > 0 && rectangle.height > 0;
  }

  const SESSION_EXPIRED_PATTERN = /(?:oturum(?:unuz|unuzun|\s+süreniz)?\s+(?:sona\s+er(?:di|miştir)|sonlan(?:dı|mıştır)|kapat(?:ıldı|ılmıştır)|dol(?:du|muştur))|oturum\s+süresi\s+(?:dol(?:du|muştur)|sona\s+er(?:di|miştir))|yeniden\s+giriş\s+yap(?:ınız|manız)?|tekrar\s+giriş\s+yap(?:ınız|manız)?|giriş\s+yapmanız\s+gerekmektedir|giriş\s+ekranına\s+yönlendir|sistemden\s+çıkış\s+(?:yapıldı|yapılmıştır|gerçekleştirildi)|çıkış\s+(?:yapıldı|gerçekleştirildi)|oturum\s+kapatıldı|zaman\s+aşımı|session\s+(?:expired|timeout|ended))/i;
  const SESSION_ALERT_SELECTORS = [
    '[role="alert"]',
    '[aria-live="assertive"]',
    '.ui-growl-message',
    '.ui-messages-error',
    '.ui-message-error',
    '.alert-danger',
    '.alert-warning',
    '.toast',
    '.toast-message',
    '.swal2-popup',
    '.p-toast-message',
    '.modal-dialog'
  ];
  let lastSessionSignalAt = 0;
  let lastSessionSignalText = '';
  let sessionSignalTimer = null;

  function sessionExpiryEvidenceFromDom({includeBodyFallback = false} = {}) {
    const candidates = [];
    for (const selector of SESSION_ALERT_SELECTORS) {
      for (const element of document.querySelectorAll(selector)) {
        if (!visible(element)) continue;
        const text = normalizedText(element.innerText || element.textContent).slice(0, 2_000);
        if (text) candidates.push(text);
      }
    }
    candidates.push(normalizedText(document.title));
    // Full-body fallback is deliberately excluded from the MutationObserver
    // hot path. On 10k+ document trees, reading body.innerText for every DOM
    // mutation is expensive. Heartbeat still enables this fallback so generic
    // timeout overlays remain detectable without turning every tree update
    // into a full-page text walk.
    if (includeBodyFallback) {
      candidates.push(normalizedText(document.body?.innerText).slice(0, 30_000));
    }

    // RC15.8.3: some UYAP logout flows replace the application shell with a
    // login form without rendering one of the historical timeout messages.
    // Treat a visible password/login form as authoritative only when the case
    // tree is no longer present, avoiding false positives from unrelated UI.
    try {
      const visiblePassword = [...document.querySelectorAll('input[type="password"]')].some(visible);
      const visibleLoginForm = [...document.querySelectorAll('form')].some((form) => {
        if (!visible(form)) return false;
        const text = normalizedText(form.innerText || form.textContent);
        return visiblePassword || /(?:giriş\s+yap|oturum\s+aç|e-devlet)/i.test(text);
      });
      const hasArchiveTree = Boolean(document.querySelector(profile.exactFileSelector)) || [...document.querySelectorAll('ul.treeview')].some(visible);
      if (visibleLoginForm && !hasArchiveTree) {
        return {
          text: 'UYAP_LOGIN_FORM_VISIBLE',
          title: normalizedText(document.title).slice(0, 300),
          url: location.href
        };
      }
    } catch {}

    for (const text of candidates) {
      const match = text.match(SESSION_EXPIRED_PATTERN);
      if (match) {
        const index = Math.max(0, match.index || 0);
        return {
          text: text.slice(Math.max(0, index - 120), Math.min(text.length, index + match[0].length + 180)),
          title: normalizedText(document.title).slice(0, 300),
          url: location.href
        };
      }
    }
    return null;
  }

  function sendSessionExpiredSignal() {
    const evidence = sessionExpiryEvidenceFromDom({includeBodyFallback:false});
    if (!evidence) return;
    const now = Date.now();
    if (evidence.text === lastSessionSignalText && now - lastSessionSignalAt < 30_000) return;
    lastSessionSignalAt = now;
    lastSessionSignalText = evidence.text;
    try {
      const result = chrome.runtime.sendMessage({
        type: MESSAGE.SESSION_EXPIRED_SIGNAL,
        evidence
      });
      result?.catch?.(() => {});
    } catch {
      // Service worker may be restarting; heartbeat remains the fallback.
    }
  }

  function scheduleSessionExpiredCheck(delay = 250) {
    clearTimeout(sessionSignalTimer);
    sessionSignalTimer = setTimeout(sendSessionExpiredSignal, delay);
  }

  async function stableHash(value) {
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(String(value)));
    return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2,'0')).join('');
  }

  function directChild(parent, selector) {
    return [...(parent?.children || [])].find((child) => child.matches?.(selector)) || null;
  }

  function tooltipText(fileElement) {
    const html = fileElement.getAttribute("data-original-title") || "";
    if (!html) return "";
    const documentFragment = new DOMParser().parseFromString(html, "text/html");
    return normalizedText(documentFragment.body.textContent);
  }

  function tooltipField(text, label, nextLabels) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const boundary = nextLabels.map((item) => item.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    const expression = new RegExp(`${escaped}\\s*:\\s*(.*?)(?=(?:${boundary})\\s*:|$)`, "i");
    return normalizedText(text.match(expression)?.[1]);
  }

  function parseMetadata(fileElement) {
    const text = tooltipText(fileElement);
    const labels = [
      "Birim Evrak No",
      "Evrakın Onaylandığı Tarih",
      "Gönderen Yer/Kişi",
      "Gönderen Dosya No",
      "Gönderen Sayı",
      "Sisteme Gönderildiği Tarih",
      "Açıklama",
      "Türü",
      "Tipi"
    ];
    const field = (name) => tooltipField(text, name, labels.filter((label) => label !== name));
    return {
      unitDocumentNumber: field("Birim Evrak No"),
      approvalDate: field("Evrakın Onaylandığı Tarih"),
      sender: field("Gönderen Yer/Kişi"),
      sentDate: field("Sisteme Gönderildiği Tarih"),
      description: field("Açıklama"),
      documentType: field("Türü"),
      uyapType: field("Tipi")
    };
  }

  function pageIdentity() {
    const bodyText = normalizedText(document.body?.innerText).slice(0, 15_000);
    const match = bodyText.match(/\b(\d{4}\/\d+)\s*-\s*(.{3,160}?Mahkemesi)\s*-/i);
    if (match) return { caseId: match[1], courtName: match[2] };

    const rootFolder = document.querySelector(`${profile.exactFolderSelector}`);
    const rootText = normalizedText(rootFolder?.textContent);
    const rootMatch = rootText.match(/^(.*?Mahkemesi)\s+(\d{4}\/\d+)$/i);
    return {
      caseId: rootMatch?.[2] || "Dosya_Bilinmiyor",
      courtName: rootMatch?.[1] || "Mahkeme_Bilinmiyor"
    };
  }

  function ancestorFolderLabels(fileElement) {
    const labels = [];
    let listItem = fileElement.closest("li");
    while (listItem) {
      const folder = directChild(listItem, profile.exactFolderSelector);
      if (folder) labels.unshift(normalizedText(folder.textContent));
      listItem = listItem.parentElement?.closest("li") || null;
    }
    return labels;
  }

  function meaningfulFolders(labels, caseId, courtName) {
    const ignored = [
      "Dosyaya Eklenen Son 20 Evrak",
      "Tüm Evraklar",
      `${courtName} ${caseId}`
    ];
    const filtered = labels.filter((label) => {
      if (!label || ignored.some((item) => normalizedText(item) === label)) return false;
      // Some live UYAP builds expose technical wrapper nodes such as
      // evrak-a9efe7520fed27d5. They are DOM identities, not user folders.
      return !/^evrak-[a-f0-9]{12,64}$/i.test(normalizedText(label));
    });
    return filtered.length ? filtered : ["Genel"];
  }

  function parentMainFile(fileElement) {
    let listItem = fileElement.closest("li")?.parentElement?.closest("li") || null;
    while (listItem) {
      const candidate = directChild(listItem, profile.exactFileSelector);
      if (candidate) return candidate;
      listItem = listItem.parentElement?.closest("li") || null;
    }
    return null;
  }

  function dateToIso(value) {
    const match = String(value || "").match(/\b(\d{2})[./-](\d{2})[./-](\d{4})\b/);
    return match ? `${match[3]}-${match[2]}-${match[1]}` : "Tarih_Bilinmiyor";
  }

  async function buildRecord(fileElement, index, identity, sourceInfo = null) {
    const ownTitle = normalizedText(fileElement.closest("li")?.dataset?.sid || fileElement.textContent) || "Adsiz Evrak";
    const attachmentParentId = normalizedText(fileElement.getAttribute("ana_evrak_id"));
    const parentFile = attachmentParentId ? parentMainFile(fileElement) : null;
    const parentTitle = normalizedText(parentFile?.closest("li")?.dataset?.sid || parentFile?.textContent);
    const metadata = parseMetadata(parentFile || fileElement);
    const encryptedToken = normalizedText(fileElement.getAttribute("evrak_id")).replace(/^"|"$/g, "");
    const sourceDocumentId = ['data-document-id','document_id','data-evrak-id','evrak_id']
      .map(name => normalizedText(fileElement.getAttribute(name))).find(value => /^\d+$/.test(value)) || null;
    const folderLabels = ancestorFolderLabels(fileElement);
    const containerLabel = folderLabels.find(label => /^\d{4}\/\d+\s*\(/.test(label)) || '';
    const containerId = containerLabel.match(/^(\d{4}\/\d+)/)?.[1] || identity.caseId;
    const identityParts = sourceDocumentId ? ['source-id',sourceDocumentId]
      : attachmentParentId ? ['parent-and-label',attachmentParentId,ownTitle]
      : metadata.unitDocumentNumber ? ['unit-number',metadata.unitDocumentNumber]
      : ['metadata-fallback',ownTitle,metadata.approvalDate,metadata.sender,folderLabels];
    const documentId = 'd-' + await stableHash(JSON.stringify([identity.courtName,identity.caseId,...identityParts]));
    const documentTitle = attachmentParentId && parentTitle ? `${parentTitle} - ${ownTitle}` : ownTitle;

    fileElement.dataset.uyapArchiveDocumentId = documentId;
    return {
      sourceSystem: sourceInfo?.sourceSystem || adapters?.SOURCE_SYSTEM?.CLASSIC || 'UYAP',
      sourceContext: sourceInfo?.sourceContext || {
        adapter: sourceInfo?.adapter || 'uyap-classic-tree-v3',
        pageUrl: location.origin + location.pathname
      },
      institutionName: sourceInfo?.institutionName || identity.courtName,
      documentId,
      sourceDocumentId,
      containerId,
      containerLabel,
      sourceIdentityHash: await stableHash(encryptedToken),
      identityEvidence: JSON.stringify(attachmentParentId ? [attachmentParentId, ownTitle] : [ownTitle, metadata.unitDocumentNumber, metadata.approvalDate, metadata.sender, metadata.documentType]),
      identityMethod: sourceDocumentId ? 'source-id' : attachmentParentId ? "parent-and-label" : metadata.unitDocumentNumber ? "unit-number" : "metadata-fallback",
      caseId: identity.caseId,
      courtName: identity.courtName,
      folderNames: meaningfulFolders(folderLabels, identity.caseId, identity.courtName),
      documentTitle,
      documentDate: dateToIso(metadata.approvalDate || documentTitle),
      extension: null,
      sourceUrl: null,
      isAttachment: Boolean(attachmentParentId),
      parentDocumentId: attachmentParentId || null,
      unitDocumentNumber: metadata.unitDocumentNumber || null,
      sender: metadata.sender || null,
      documentType: metadata.documentType || null,
      uyapType: metadata.uyapType || null
    };
  }

  function recordScore(record) {
    const meaningful = record.folderNames.filter((name) => name !== "Genel").length;
    return meaningful * 10 + Number(Boolean(record.unitDocumentNumber)) + Number(Boolean(record.documentType));
  }

  function kurumClassicCaseScope(treeRoot, caseId) {
    const wanted = normalizedText(caseId).replace(/\s+/g, '');
    if (!treeRoot || !/^20\d{2}\/\d{1,10}$/.test(wanted)) return null;
    const matches = [];
    for (const item of [...treeRoot.querySelectorAll('li')]) {
      const labelCandidates = [];
      // Legacy Treeview skins differ slightly: some put span.folder directly
      // under LI, others wrap it in a DIV next to the hitarea. Only inspect the
      // item's own header, never descendant case branches.
      for (const selector of [
        ':scope > span.folder', ':scope > span.file', ':scope > a',
        ':scope > [data-sid]', ':scope > [role="treeitem"]',
        ':scope > div > span.folder', ':scope > div > span.file', ':scope > div > a',
        ':scope > div > [data-sid]', ':scope > div > [role="treeitem"]'
      ]) {
        try {
          const node = item.querySelector(selector);
          if (node) labelCandidates.push(node);
        } catch {}
      }
      const label = labelCandidates
        .map((child) => normalizedText(child.textContent || child.getAttribute?.('data-sid')))
        .find((value) => /^20\d{2}\s*[\/-]\s*\d{1,10}\s*\(/.test(value)) ||
        normalizedText(item.getAttribute?.('data-sid'));
      if (!label) continue;
      const match = label.match(/^\s*(20\d{2})\s*[\/-]\s*(\d{1,10})\s*\(/);
      if (!match) continue;
      const found = `${match[1]}/${match[2]}`;
      if (found === wanted) matches.push(item);
    }
    return matches.length === 1 ? matches[0] : null;
  }

  async function scanClassicPage(expand = true, detected = null) {
    const roots = [...document.querySelectorAll('ul.treeview')].filter(visible)
      .filter(root => !root.parentElement?.closest('ul.treeview'));
    if (roots.length > 1) throw new Error('Birden çok görünür evrak ağacı var. Yalnızca bir dava/Evrak ekranı açık olmalı.');
    const treeRoot = roots[0] || document;
    const isKurumClassic = detected?.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM;
    const identity = isKurumClassic ? effectiveKurumIdentity() : pageIdentity();
    let root = treeRoot;
    let inventoryScope = 'expanded-visible-tree';
    if (isKurumClassic && treeRoot !== document) {
      const activeCaseId = normalizedText(identity.caseId).replace(/\s+/g, '');
      if (!/^20\d{2}\/\d{1,10}$/.test(activeCaseId)) {
        throw new Error('UYAP_KURUM_ACTIVE_CASE_ID_NOT_CONFIRMED: Açık UYAP Kurum dosyasının görünür modal kimliği doğrulanamadı; başka dosyanın evraklarını karıştırmamak için tarama durduruldu.');
      }
      const scoped = kurumClassicCaseScope(treeRoot, activeCaseId);
      if (!scoped) {
        throw new Error(`UYAP_KURUM_ACTIVE_CASE_TREE_NOT_FOUND: Görünür açık dosya ${activeCaseId} için “Tüm Evraklar” altında tek bir dava dalı doğrulanamadı; başka dosyanın evraklarını karıştırmamak için tarama durduruldu.`);
      }
      root = scoped;
      inventoryScope = 'expanded-active-kurum-case-tree';
    }
    // Never declare a paginated inventory complete by reading only its first page.
    const pageCount = normalizedText(document.body?.innerText).match(/Toplam\s+(\d+)\s+sayfadan/i);
    if (pageCount && Number(pageCount[1]) > 1) throw new Error('UNSUPPORTED_PAGINATION: Bu ekran birden çok sayfa içeriyor; eksiksiz tarama doğrulanamadı.');
    const expansion = expand ? await globalThis.UYAP_EXPAND_TREE({
      root, visible, style: getComputedStyle, sleep
    }) : {settled: null};
    const fileElements = [...root.querySelectorAll(profile.exactFileSelector)];
    const sourceInfo = detected?.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM
      ? {
          ...detected,
          institutionName: identity.courtName,
          sourceContext: {
            adapter: 'uyap-kurum-classic-tree-fallback-v1',
            pageUrl: location.origin + location.pathname,
            institutionName: identity.courtName,
            caseId: identity.caseId
          }
        }
      : detected;
    const records = await Promise.all(fileElements.map((element, index) => buildRecord(element, index, identity, sourceInfo)));
    const unique = new Map();
    for (const record of records) {
      const existing = unique.get(record.documentId);
      if (existing && existing.identityEvidence !== record.identityEvidence) {
        throw new Error("Aynı kimliğe birden çok farklı kaynak evrak bağlandı; tekilleştirme durduruldu.");
      }
      if (existing && record.identityMethod === 'parent-and-label' && existing.containerLabel === record.containerLabel && existing.sourceIdentityHash !== record.sourceIdentityHash) {
        throw new Error('AMBIGUOUS_ATTACHMENT_ID: Aynı adlı iki ekin kaynak kimliği ayırt edilemiyor; dosya atlanmadı, tarama durduruldu.');
      }
      if (!existing || recordScore(record) > recordScore(existing)) unique.set(record.documentId, record);
    }

    const documents = [...unique.values()];
    return {
      documents,
      diagnostics: {
        parser: detected?.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM ? 'uyap-kurum-classic-tree-fallback-v1' : "uyap-jquery-tree-v3",
        sourceSystem: detected?.sourceSystem || adapters?.SOURCE_SYSTEM?.CLASSIC || 'UYAP',
        sourceAdapter: sourceInfo?.sourceContext?.adapter || detected?.adapter || 'uyap-classic-tree-v3',
        contentVersion: VERSION,
        inventoryScope,
        sourceIdDocuments: documents.filter(item => item.sourceDocumentId).length,
        containers: [...new Set(documents.map(item => item.containerLabel).filter(Boolean))],
        expansion,
        fileNodes: fileElements.length,
        uniqueDocuments: documents.length,
        mainDocuments: documents.filter((item) => !item.isAttachment).length,
        attachments: documents.filter((item) => item.isAttachment).length,
        folderNodes: document.querySelectorAll(profile.exactFolderSelector).length,
        saveMenuItems: [...document.querySelectorAll(profile.saveMenuSelector)].filter((item) => normalizedText(item.textContent) === "Kaydet").length,
        detectedCaseId: identity.caseId,
        detectedCourt: identity.courtName,
        pageUrl: location.origin + location.pathname
      }
    };
  }


  function dispatchScroll(element) {
    if (!element?.dispatchEvent) return;
    try {
      const ScrollEvent = globalThis.Event;
      element.dispatchEvent(typeof ScrollEvent === 'function' ? new ScrollEvent('scroll', {bubbles:true}) : {type:'scroll'});
    } catch {
      try { element.dispatchEvent({type:'scroll'}); } catch {}
    }
  }
  function dispatchClick(element) {
    if (!element) return false;

    // Kurum UYAP uses anchors whose href is javascript:void(0) while the real
    // action is attached with jQuery. Native element.click() also executes the
    // javascript: URL, which Chrome can block under CSP. Dispatch the DOM event
    // directly and suppress only the javascript: default navigation. This keeps
    // the portal's registered click listeners alive without asking CSP to run
    // an inline/javascript URL.
    try {
      const tag = String(element.tagName || '').toUpperCase();
      const type = String(element.getAttribute?.('type') || '').toLowerCase();
      if (tag === 'INPUT' && ['checkbox','radio'].includes(type) && typeof element.click === 'function') {
        element.click();
        return true;
      }

      const href = String(element.getAttribute?.('href') || '');
      const blocksJavascriptNavigation = /^\s*javascript\s*:/i.test(href);
      const preventJavascriptNavigation = (event) => { event.preventDefault(); };
      if (blocksJavascriptNavigation) {
        element.addEventListener('click', preventJavascriptNavigation, {capture:true, once:true});
      }

      const rect = element.getBoundingClientRect?.() || {left:0,top:0,width:1,height:1};
      const dispatched = element.dispatchEvent(new MouseEvent('click', {
        bubbles:true,cancelable:true,button:0,buttons:0,view:window,
        clientX:rect.left + Math.max(1, rect.width / 2),
        clientY:rect.top + Math.max(1, rect.height / 2)
      }));

      if (blocksJavascriptNavigation) {
        try { element.removeEventListener('click', preventJavascriptNavigation, true); } catch {}
      }
      // A portal listener is allowed to preventDefault(); that does not mean the
      // click failed. Reaching dispatchEvent without an exception is sufficient.
      void dispatched;
      return true;
    } catch { return false; }
  }

  function kurumRowFingerprint() {
    const rows = adapters.kurumRows(document, visible);
    const parts = rows.slice(0, 12).map((row) => {
      const candidate = adapters.extractKurumCandidate(row, document, visible);
      return [candidate.sourceDocumentId || candidate.sourceToken || '', candidate.documentTitle || '', candidate.documentDate || ''].join('|');
    });
    return `${rows.length}:${parts.join('||')}`;
  }

  async function waitForKurumViewChange(previousFingerprint, timeoutMs = 4_000) {
    const deadline = Date.now() + Math.max(500, timeoutMs);
    while (Date.now() < deadline) {
      await sleep(80);
      const current = kurumRowFingerprint();
      if (current && current !== previousFingerprint) return true;
    }
    return false;
  }

  async function buildKurumRecord(row, index, identity, pageIndex = 0, virtual = false) {
    const candidate = adapters.extractKurumCandidate(row, document, visible);
    if (!candidate.action) return null;
    const stableEvidence = candidate.sourceToken || candidate.identityEvidence || `${candidate.documentTitle}|${candidate.documentDate}|${pageIndex}|${index}`;
    const sourceDocumentId = candidate.sourceDocumentId || null;
    if (virtual && !sourceDocumentId && !candidate.sourceToken) {
      throw new Error('UYAP_KURUM_VIRTUAL_ROW_ID_REQUIRED: Sanal UYAP Kurum gridinde evrak satırı için kalıcı kaynak kimliği bulunamadı; yanlış eşleme riski nedeniyle işlem durduruldu.');
    }
    const fallbackLocatorEvidence = JSON.stringify([stableEvidence, pageIndex, index]);
    const documentId = 'd-' + await stableHash(JSON.stringify([
      adapters.SOURCE_SYSTEM.KURUM,
      identity.institutionName,
      identity.caseId,
      sourceDocumentId ? ['source-id', sourceDocumentId] : candidate.sourceToken ? ['source-token', stableEvidence] : ['row-evidence', fallbackLocatorEvidence]
    ]));
    const sourceIdentityHash = await stableHash(candidate.sourceToken || sourceDocumentId ? stableEvidence : fallbackLocatorEvidence);
    row.dataset.uyapArchiveDocumentId = documentId;
    // A source-id-less Kurum grid can reuse the same disabled toolbar element
    // for every visible row. Do not bind that placeholder action to a document
    // during inventory discovery; the real action is resolved after row selection.
    if (candidate.actionMode !== 'global-toolbar-select') {
      candidate.action.dataset.uyapArchiveKurumActionFor = documentId;
    }
    return {
      sourceSystem: adapters.SOURCE_SYSTEM.KURUM,
      sourceContext: {
        ...identity.sourceContext,
        kurumLocator: {
          pageIndex,
          virtual:Boolean(virtual),
          sourceDocumentId,
          sourceIdentityHash,
          actionMode:candidate.actionMode || 'row-action'
        }
      },
      institutionName: identity.institutionName,
      documentId,
      sourceDocumentId,
      containerId: identity.caseId,
      containerLabel: candidate.folderName,
      sourceIdentityHash,
      identityEvidence: candidate.identityEvidence,
      identityMethod: sourceDocumentId ? 'source-id' : candidate.sourceToken ? 'source-token' : 'row-evidence-indexed',
      caseId: identity.caseId,
      courtName: identity.courtName,
      folderNames: [candidate.folderName || 'Kurum Evrakları'],
      documentTitle: candidate.documentTitle,
      documentDate: candidate.documentDate,
      extension: null,
      sourceUrl: null,
      isAttachment: false,
      parentDocumentId: null,
      unitDocumentNumber: null,
      sender: null,
      documentType: null,
      uyapType: null
    };
  }

  async function collectKurumVisibleRecords(identity, pageIndex = 0, virtual = false) {
    const rows = adapters.kurumRows(document, visible);
    const records = [];
    for (let index = 0; index < rows.length; index += 1) {
      const record = await buildKurumRecord(rows[index], index, identity, pageIndex, virtual);
      if (record) records.push(record);
    }
    return records;
  }

  function mergeKurumRecords(target, records) {
    for (const record of records) {
      const existing = target.get(record.documentId);
      if (existing && existing.identityEvidence !== record.identityEvidence) {
        throw new Error('UYAP_KURUM_AMBIGUOUS_ID: Aynı kimliğe birden çok farklı evrak bağlandı; tarama durduruldu.');
      }
      target.set(record.documentId, record);
    }
  }

  async function moveKurumToFirstPage(maxSteps = 100) {
    let steps = 0;
    while (steps < maxSteps) {
      const pager = adapters.kurumPager(document, visible);
      if (!pager.prevEnabled || !pager.prev) return true;
      const previous = kurumRowFingerprint();
      if (!dispatchClick(pager.prev)) return false;
      if (!await waitForKurumViewChange(previous, 3_000)) return false;
      steps += 1;
    }
    return false;
  }

  async function moveKurumToPage(pageIndex, maxSteps = 100) {
    if (!Number.isInteger(pageIndex) || pageIndex <= 0) return moveKurumToFirstPage(maxSteps);
    if (!await moveKurumToFirstPage(maxSteps)) return false;
    for (let index = 0; index < pageIndex; index += 1) {
      const pager = adapters.kurumPager(document, visible);
      if (!pager.nextEnabled || !pager.next) return false;
      const previous = kurumRowFingerprint();
      if (!dispatchClick(pager.next)) return false;
      if (!await waitForKurumViewChange(previous, 4_000)) return false;
    }
    return true;
  }

  async function collectKurumVirtualCurrentView(identity, diagnostics, pageIndex = 0, {requireDeclared = false} = {}) {
    const viewport = adapters.kurumVirtualViewport(document, visible);
    const declared = Number(diagnostics.declaredRowCount || 0);
    if (!viewport || !(Number(viewport.scrollHeight || 0) > Number(viewport.clientHeight || 0))) {
      throw new Error(`UYAP_KURUM_VIRTUAL_GRID_UNSUPPORTED: Sanal grid algılandı fakat kaydırılabilir viewport doğrulanamadı. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    if (requireDeclared && declared <= 0) {
      throw new Error(`UYAP_KURUM_VIRTUAL_GRID_UNSUPPORTED: Sanal grid tam kayıt sayısını bildirmiyor; eksik envanter riski nedeniyle iş başlatılmadı. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    const unique = new Map();
    const originalScrollTop = Number(viewport.scrollTop || 0);
    const maxScroll = () => Math.max(0, Number(viewport.scrollHeight || 0) - Number(viewport.clientHeight || 0));
    let noProgress = 0;
    let steps = 0;
    try {
      viewport.scrollTop = 0;
      dispatchScroll(viewport);
      await sleep(120);
      while (steps < 2_000) {
        const before = unique.size;
        mergeKurumRecords(unique, await collectKurumVisibleRecords(identity, pageIndex, true));
        if (requireDeclared && declared > 0 && unique.size >= declared) break;
        noProgress = unique.size === before ? noProgress + 1 : 0;
        const current = Number(viewport.scrollTop || 0);
        const maximum = maxScroll();
        if (current >= maximum - 1 && noProgress >= 2) break;
        const step = Math.max(120, Math.floor(Number(viewport.clientHeight || 400) * 0.8));
        const nextTop = Math.min(maximum, current + step);
        if (nextTop === current && noProgress >= 1) break;
        viewport.scrollTop = nextTop;
        dispatchScroll(viewport);
        await sleep(90);
        steps += 1;
      }
      if (requireDeclared && declared > 0 && unique.size < declared) {
        throw new Error(`UYAP_KURUM_VIRTUAL_GRID_INCOMPLETE: Grid ${declared} kayıt bildiriyor fakat güvenli taramada ${unique.size} benzersiz evraka erişildi. Eksik arşiv başlatılmadı. Tanılama: ${JSON.stringify(diagnostics)}`);
      }
      return {records:[...unique.values()], virtualSteps:steps};
    } finally {
      viewport.scrollTop = originalScrollTop;
      dispatchScroll(viewport);
    }
  }

  async function collectKurumPagedRecords(identity, diagnostics, {virtualWithinPage = false} = {}) {
    if (!await moveKurumToFirstPage()) {
      throw new Error(`UYAP_KURUM_PAGINATION_NAVIGATION_FAILED: İlk sayfaya güvenli biçimde dönülemedi. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    const unique = new Map();
    const seenFingerprints = new Set();
    const declaredPages = Math.max(1, Number(diagnostics.pageCount || 1));
    const hardLimit = Math.min(500, Math.max(declaredPages + 2, 25));
    let pageIndex = 0;
    let virtualSteps = 0;
    try {
      while (pageIndex < hardLimit) {
        const fingerprint = kurumRowFingerprint();
        if (seenFingerprints.has(fingerprint) && pageIndex > 0) {
          throw new Error(`UYAP_KURUM_PAGINATION_LOOP: Sayfalama aynı evrak görünümüne geri döndü; eksik envanterle devam edilmedi. Tanılama: ${JSON.stringify(diagnostics)}`);
        }
        seenFingerprints.add(fingerprint);
        if (virtualWithinPage) {
          const virtualResult = await collectKurumVirtualCurrentView(identity, diagnostics, pageIndex, {requireDeclared:false});
          mergeKurumRecords(unique, virtualResult.records);
          virtualSteps += virtualResult.virtualSteps;
        } else {
          mergeKurumRecords(unique, await collectKurumVisibleRecords(identity, pageIndex, false));
        }
        const pager = adapters.kurumPager(document, visible);
        if (!pager.nextEnabled || !pager.next) break;
        const previous = kurumRowFingerprint();
        if (!dispatchClick(pager.next) || !await waitForKurumViewChange(previous, 4_000)) {
          throw new Error(`UYAP_KURUM_PAGINATION_NAVIGATION_FAILED: Sonraki sayfaya geçilemedi. Tanılama: ${JSON.stringify(diagnostics)}`);
        }
        pageIndex += 1;
      }
      const pager = adapters.kurumPager(document, visible);
      if (pageIndex >= hardLimit - 1 && pager.nextEnabled) {
        throw new Error(`UYAP_KURUM_PAGINATION_LIMIT: Sayfalama güvenlik sınırını aştı. Tanılama: ${JSON.stringify(diagnostics)}`);
      }
      const declared = Number(diagnostics.declaredRowCount || 0);
      if (declared > 0 && unique.size < declared) {
        throw new Error(`UYAP_KURUM_PAGED_INVENTORY_INCOMPLETE: Ekran ${declared} kayıt bildiriyor fakat tüm sayfalarda ${unique.size} benzersiz evraka erişildi. Eksik arşiv başlatılmadı. Tanılama: ${JSON.stringify(diagnostics)}`);
      }
      return {records:[...unique.values()], pagesScanned:pageIndex + 1, virtualSteps};
    } finally {
      try { await moveKurumToFirstPage(); } catch {}
    }
  }

  async function collectKurumVirtualRecords(identity, diagnostics) {
    return collectKurumVirtualCurrentView(identity, diagnostics, 0, {requireDeclared:true});
  }

  async function scanKurumPage(detected) {
    const identity = effectiveKurumIdentity();
    const classicNodes = document.querySelectorAll(profile.exactFileSelector).length;
    const viewKind = adapters.kurumViewKind?.(document, location) || 'unknown';
    const immediateRows = adapters.kurumRows(document, visible);
    // Live kurum.uyap.gov.tr renders the opened case as the legacy jQuery
    // Treeview (#browser / span.file[evrak_id]). Those span nodes also match the
    // generic adaptive-row selector, so checking immediateRows alone misroutes
    // the page into the grid parser. Prefer the verified tree parser whenever
    // the Kurum view itself is classified as classic-tree.
    if (classicNodes && viewKind === 'classic-tree') return scanClassicPage(true, detected);
    if (classicNodes && !immediateRows.length) return scanClassicPage(true, detected);

    const diagnostics = {
      ...adapters.kurumDiagnostics(document, location, visible),
      parser:adapters.KURUM_ADAPTER_VERSION || 'uyap-kurum-adaptive-v11',
      sourceSystem:adapters.SOURCE_SYSTEM.KURUM,
      sourceAdapter:adapters.KURUM_ADAPTER_VERSION || 'uyap-kurum-adaptive-v11',
      contentVersion:VERSION,
      detectedCaseId:identity.caseId,
      detectedCourt:identity.courtName,
      detectedInstitution:identity.institutionName,
      caseIdentityMode:identity.caseIdentityMode,
      explicitCaseIdDetected:Boolean(identity.explicitCaseId)
    };

    if (diagnostics.viewKind === 'case-list') {
      throw new Error(`UYAP_KURUM_CASE_OPEN_REQUIRED: UYAP Kurum dosya listesi açık. Arşivlenecek dosya UYAP Archive içinden güvenli biçimde seçilmeli; dosya içeriği açıldıktan sonra evrak taraması başlayacak. Bu listedeki dosya satırları evrak olarak sayılmadı. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    if (diagnostics.viewKind === 'portal-shell') {
      diagnostics.inventoryScope = 'kurum-portal-shell';
      diagnostics.pagesScanned = 0;
      diagnostics.virtualSteps = 0;
      diagnostics.sourceIdDocuments = 0;
      diagnostics.uniqueDocuments = 0;
      return {documents:[], diagnostics};
    }

    const hasPagination = Number(diagnostics.nextPageSignals || 0) > 0 || Number(diagnostics.pageCount || 1) > 1;
    const hasVirtual = Number(diagnostics.virtualViewportSignals || 0) > 0;

    let records = [];
    let inventoryScope = 'visible-kurum-document-table';
    let pagesScanned = 1;
    let virtualSteps = 0;
    if (hasPagination) {
      const result = await collectKurumPagedRecords(identity, diagnostics, {virtualWithinPage:hasVirtual});
      records = result.records;
      pagesScanned = result.pagesScanned;
      virtualSteps = result.virtualSteps || 0;
      inventoryScope = hasVirtual ? 'all-pages-full-virtual-kurum-grid' : 'all-pages-kurum-document-table';
    } else if (hasVirtual) {
      const result = await collectKurumVirtualRecords(identity, diagnostics);
      records = result.records;
      virtualSteps = result.virtualSteps;
      inventoryScope = 'full-virtual-kurum-grid';
    } else {
      records = await collectKurumVisibleRecords(identity, 0, false);
    }

    const unique = new Map();
    mergeKurumRecords(unique, records);
    const documents = [...unique.values()];
    diagnostics.inventoryScope = inventoryScope;
    diagnostics.pagesScanned = pagesScanned;
    diagnostics.virtualSteps = virtualSteps;
    diagnostics.sourceIdDocuments = documents.filter((item) => item.sourceDocumentId).length;
    diagnostics.uniqueDocuments = documents.length;

    if (!documents.length) {
      if (classicNodes) return scanClassicPage(true, detected);
      throw new Error(`UYAP_KURUM_STRUCTURE_UNRECOGNIZED: UYAP Kurum evrak yapısı güvenli biçimde tanınamadı. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    if (!identity.caseId) {
      throw new Error(`UYAP_KURUM_SOURCE_SCOPE_UNKNOWN: Kurum arşiv görünümü için güvenli kaynak kimliği üretilemedi. Tanılama: ${JSON.stringify(diagnostics)}`);
    }
    return {documents, diagnostics};
  }

  async function locateKurumTarget(documentId, locator = null) {
    const directAction = document.querySelector(`[data-uyap-archive-kurum-action-for="${CSS.escape(documentId)}"]`);
    const directRow = document.querySelector(`[data-uyap-archive-document-id="${CSS.escape(documentId)}"]`);
    if (directAction && directRow) return {row:directRow, action:directAction, actionMode:locator?.actionMode || 'row-action'};

    const findOnCurrentView = async () => {
      const rows = adapters.kurumRows(document, visible);
      for (const row of rows) {
        const candidate = adapters.extractKurumCandidate(row, document, visible);
        if (!candidate.action) continue;
        const evidence = candidate.sourceToken || candidate.identityEvidence;
        const rowIndex = rows.indexOf(row);
        const hashInput = candidate.sourceToken || candidate.sourceDocumentId ? evidence : JSON.stringify([evidence, Number(locator?.pageIndex || 0), rowIndex]);
        const hash = evidence ? await stableHash(hashInput) : null;
        const sourceMatch = locator?.sourceDocumentId && candidate.sourceDocumentId && String(candidate.sourceDocumentId) === String(locator.sourceDocumentId);
        const hashMatch = locator?.sourceIdentityHash && hash === locator.sourceIdentityHash;
        if (!sourceMatch && !hashMatch) continue;
        row.dataset.uyapArchiveDocumentId = documentId;
        candidate.action.dataset.uyapArchiveKurumActionFor = documentId;
        return {row, action:candidate.action, actionMode:candidate.actionMode || locator?.actionMode || 'row-action'};
      }
      return null;
    };

    if (locator?.virtual) {
      if (Number.isInteger(locator?.pageIndex) && locator.pageIndex > 0) {
        if (!await moveKurumToPage(locator.pageIndex)) return null;
      } else if (Number.isInteger(locator?.pageIndex) && locator.pageIndex === 0) {
        await moveKurumToFirstPage();
      }
      const viewport = adapters.kurumVirtualViewport(document, visible);
      if (!viewport) return null;
      const original = Number(viewport.scrollTop || 0);
      try {
        viewport.scrollTop = 0; dispatchScroll(viewport); await sleep(100);
        let noProgress = 0;
        for (let stepIndex = 0; stepIndex < 2_000; stepIndex += 1) {
          const found = await findOnCurrentView();
          if (found) return found;
          const current = Number(viewport.scrollTop || 0);
          const maximum = Math.max(0, Number(viewport.scrollHeight || 0) - Number(viewport.clientHeight || 0));
          if (current >= maximum - 1) {
            noProgress += 1;
            if (noProgress >= 2) break;
          }
          const step = Math.max(120, Math.floor(Number(viewport.clientHeight || 400) * 0.8));
          viewport.scrollTop = Math.min(maximum, current + step);
          dispatchScroll(viewport);
          await sleep(80);
        }
      } finally {
        // Keep the target visible when found; restore only on failure.
        const found = document.querySelector(`[data-uyap-archive-document-id="${CSS.escape(documentId)}"]`);
        if (!found) { viewport.scrollTop = original; dispatchScroll(viewport); }
      }
      return findOnCurrentView();
    }

    if (Number.isInteger(locator?.pageIndex) && locator.pageIndex >= 0) {
      if (!await moveKurumToPage(locator.pageIndex)) return null;
      return findOnCurrentView();
    }
    return findOnCurrentView();
  }

  function formatCensus() {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    let root = document;
    let inventoryScope = 'visible-document';
    let caseConfirmed = false;
    if (detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM && (adapters.kurumViewKind?.(document, location) || '') === 'classic-tree') {
      const roots = [...document.querySelectorAll('ul.treeview')].filter(visible).filter(node => !node.parentElement?.closest('ul.treeview'));
      const tree = roots[0] || null;
      const identity = effectiveKurumIdentity();
      const caseId = normalizedText(identity.caseId).replace(/\s+/g,'');
      if (tree && /^20\d{2}\/\d{1,10}$/.test(caseId)) {
        const scoped = kurumClassicCaseScope(tree, caseId);
        if (scoped) { root = scoped; inventoryScope='active-kurum-case-tree'; caseConfirmed=true; }
      }
    } else {
      const roots=[...document.querySelectorAll('ul.treeview')].filter(visible).filter(node=>!node.parentElement?.closest('ul.treeview'));
      if (roots.length===1) {root=roots[0];inventoryScope='visible-tree';caseConfirmed=true;}
    }
    const nodes=[...root.querySelectorAll(profile.exactFileSelector)];
    const typeCounts={}, uyapTypeCounts={}, extensionHints={}, structureCounts={main:0,attachment:0};
    const bump=(obj,key)=>{key=normalizedText(key||'Bilinmiyor').slice(0,100)||'Bilinmiyor';obj[key]=(obj[key]||0)+1;};
    const extRx=/\.([a-z0-9]{2,8})(?:\s|$|[?#])/ig;
    for(const node of nodes){
      const meta=parseMetadata(node);
      bump(typeCounts,meta.documentType||'Bilinmiyor');
      bump(uyapTypeCounts,meta.uyapType||'Bilinmiyor');
      const attachment=Boolean(node.getAttribute('ana_evrak_id'));
      structureCounts[attachment?'attachment':'main']+=1;
      const text=tooltipText(node);
      for(const match of text.matchAll(extRx)){
        const ext='.'+String(match[1]||'').toLowerCase();
        if(['.pdf','.udf','.tif','.tiff','.jpg','.jpeg','.png','.gif','.bmp','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.rtf','.xml','.odt','.ods','.odp','.zip','.7z','.rar'].includes(ext)) bump(extensionHints,ext);
      }
    }
    return {
      sourceSystem:detected.sourceSystem||'UYAP', contentVersion:VERSION, inventoryScope, caseConfirmed,
      totalNodes:nodes.length, structureCounts, typeCounts, uyapTypeCounts, extensionHints,
      riskSignals:{unknownUyapType:Number(uyapTypeCounts.Bilinmiyor||0),unknownDocumentType:Number(typeCounts.Bilinmiyor||0)},
      privacy:{rawTitlesIncluded:false,rawTokensIncluded:false,rawUrlsIncluded:false}
    };
  }

  function sourceDiagnostic() {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP', adapter:'uyap-classic-tree-v3'};
    if (detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM) {
      const identity = effectiveKurumIdentity();
      const diagnostics = adapters.kurumDiagnostics(document, location, visible);
      const candidateRows = Number(diagnostics.candidateRows || 0);
      const declaredRowCount = Number(diagnostics.declaredRowCount || 0);
      const pageCount = Number(diagnostics.pageCount || 1);
      const virtualSignals = Number(diagnostics.virtualViewportSignals || 0);
      const nextPageSignals = Number(diagnostics.nextPageSignals || 0);
      const nextPageEnabledSignals = Number(diagnostics.nextPageEnabledSignals || 0);
      const hasPagination = pageCount > 1 || nextPageSignals > 0;
      const hasVirtual = virtualSignals > 0;
      const virtualTraversalSupported = !hasVirtual || (declaredRowCount > 0 && Boolean(diagnostics.virtualViewportScrollable));
      const paginationTraversalSupported = !hasPagination || nextPageSignals > 0 || nextPageEnabledSignals > 0;
      const inventoryReachable = candidateRows > 0 && virtualTraversalSupported && paginationTraversalSupported;
      return {
        sourceSystem: adapters.SOURCE_SYSTEM.KURUM,
        sourceAdapter: diagnostics.adapter || detected.adapter || 'uyap-kurum-adaptive-v11',
        contentVersion: VERSION,
        confidence: detected.confidence || null,
        pageUrl: diagnostics.pageUrl || `${location.origin}${location.pathname}`,
        identity: {
          caseIdDetected: Boolean(identity.caseId && identity.caseId !== 'Dosya_Bilinmiyor'),
          explicitCaseIdDetected: Boolean(identity.explicitCaseId),
          caseIdentityMode: identity.caseIdentityMode || null,
          institutionDetected: Boolean(identity.institutionName && identity.institutionName !== 'UYAP Kurum')
        },
        structure: {
          viewKind: diagnostics.viewKind || adapters.kurumViewKind?.(document, location) || 'unknown',
          candidateRows,
          candidateDownloadActions: Number(diagnostics.candidateDownloadActions || 0),
          sourceTokenRows: Number(diagnostics.sourceTokenRows || 0),
          tableRows: Number(diagnostics.tableRows || 0),
          pageCount,
          declaredRowCount,
          framesHint: Boolean(diagnostics.framesHint),
          virtualViewportSignals: Number(diagnostics.virtualViewportSignals || 0),
          nextPageSignals,
          nextPageEnabledSignals,
          prevPageSignals: Number(diagnostics.prevPageSignals || 0),
          virtualViewportScrollable: Boolean(diagnostics.virtualViewportScrollable),
          globalToolbarDownload: Boolean(diagnostics.globalToolbarDownload),
          globalToolbarDownloadEnabled: Boolean(diagnostics.globalToolbarDownloadEnabled),
          globalToolbarDownloadCandidates: Number(diagnostics.globalToolbarDownloadCandidates || 0),
          selectableToolbarRows: Number(diagnostics.selectableToolbarRows || 0),
          frameworkHints: diagnostics.frameworkHints || {},
          samples: diagnostics.samples || []
        },
        capabilities: {
          canDiscoverRows: candidateRows > 0,
          canReachDownloadAction: Number(diagnostics.candidateDownloadActions || 0) > 0 || Boolean(diagnostics.globalToolbarDownload),
          singlePage: pageCount <= 1,
          paginationDetected: hasPagination,
          paginationTraversalSupported,
          virtualGridDetected: hasVirtual || (declaredRowCount > candidateRows && declaredRowCount > 0),
          virtualTraversalSupported,
          combinedPaginationVirtualSupported: hasPagination && hasVirtual ? paginationTraversalSupported && virtualTraversalSupported : true,
          globalToolbarModeSupported: Boolean(diagnostics.globalToolbarDownload) || Number(diagnostics.selectableToolbarRows || 0) > 0,
          fullInventoryReachable: inventoryReachable
        }
      };
    }

    const bodyText = normalizedText(document.body?.innerText || document.body?.textContent || '');
    const pageCount = Number(bodyText.match(/Toplam\s+(\d+)\s+sayfadan/i)?.[1] || 1);
    const fileNodes = document.querySelectorAll(profile.exactFileSelector).length;
    const treeRoots = [...document.querySelectorAll('ul.treeview')].filter(visible).length;
    const saveMenuItems = [...document.querySelectorAll(profile.saveMenuSelector)]
      .filter((item) => normalizedText(item.textContent) === 'Kaydet').length;
    return {
      sourceSystem: adapters?.SOURCE_SYSTEM?.CLASSIC || 'UYAP',
      sourceAdapter: detected.adapter || 'uyap-classic-tree-v3',
      contentVersion: VERSION,
      confidence: detected.confidence || null,
      pageUrl: `${location.origin}${String(location.pathname || '/').split('/').map((segment) => (!segment ? '' : /\d/.test(segment) || segment.length > 64 ? '<id>' : segment.slice(0, 64))).join('/') || '/'}`,
      structure: {
        treeRoots,
        fileNodes,
        folderNodes: document.querySelectorAll(profile.exactFolderSelector).length,
        saveMenuItems,
        pageCount,
        framesHint: Boolean(document.querySelector('iframe'))
      },
      capabilities: {
        canDiscoverRows: fileNodes > 0,
        canReachDownloadAction: fileNodes > 0,
        singlePage: pageCount <= 1,
        fullInventoryReachable: fileNodes > 0 && pageCount <= 1,
        virtualGridDetected: false
      }
    };
  }

  function probePage() {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP', adapter:'uyap-classic-tree-v3'};
    if (detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM) {
      const identity = effectiveKurumIdentity();
      const viewKind = adapters.kurumViewKind?.(document, location) || 'unknown';
      let hasDocuments = false;
      let activeBranchFound = false;
      let scopedDocumentCount = 0;

      if (viewKind === 'classic-tree') {
        const roots = [...document.querySelectorAll('ul.treeview')].filter(visible)
          .filter((root) => !root.parentElement?.closest('ul.treeview'));
        const treeRoot = roots.length === 1 ? roots[0] : null;
        const scoped = treeRoot ? kurumClassicCaseScope(treeRoot, identity.caseId) : null;
        activeBranchFound = Boolean(scoped);
        scopedDocumentCount = scoped ? scoped.querySelectorAll(profile.exactFileSelector).length : 0;
        hasDocuments = activeBranchFound && scopedDocumentCount > 0;
      } else if (!['case-list','portal-shell'].includes(viewKind)) {
        const actionRows = adapters.kurumRows(document, visible).some((row) => Boolean(adapters.extractKurumCandidate(row, document, visible)?.action));
        scopedDocumentCount = document.querySelectorAll(profile.exactFileSelector).length;
        hasDocuments = actionRows || scopedDocumentCount > 0;
      }

      return {
        sourceSystem: adapters.SOURCE_SYSTEM.KURUM,
        caseId: identity.caseId,
        courtName: identity.courtName,
        institutionName: identity.institutionName,
        identitySource: identity.identitySource || identity.caseIdentityMode || null,
        caseIdentityMode: identity.caseIdentityMode || null,
        hasDocuments,
        viewKind,
        activeBranchFound,
        scopedDocumentCount,
        caseListRowCount: viewKind === 'case-list' ? adapters.kurumCaseRows?.(document, visible)?.length || 0 : 0,
        contentVersion: VERSION
      };
    }
    const identity = pageIdentity();
    return {
      sourceSystem: adapters?.SOURCE_SYSTEM?.CLASSIC || 'UYAP',
      caseId: identity.caseId,
      courtName: identity.courtName,
      institutionName: identity.courtName,
      hasDocuments: document.querySelectorAll(profile.exactFileSelector).length > 0,
      contentVersion: VERSION
    };
  }

  async function scanPage(expand = true) {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP', adapter:'uyap-classic-tree-v3'};
    if (detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM) return scanKurumPage(detected);
    return scanClassicPage(expand, detected);
  }

  function listKurumCases() {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    const viewKind = adapters.kurumViewKind?.(document, location) || 'unknown';
    const canOpenCaseList = Boolean(adapters.kurumCaseListAction?.(document, visible));
    if (detected.sourceSystem !== adapters?.SOURCE_SYSTEM?.KURUM || viewKind !== 'case-list') {
      return {viewKind, cases:[], canOpenCaseList};
    }
    const cases=(adapters.kurumCaseTargets?.(document, visible) || []).map(({caseId,courtName,caseType,caseStatus,label})=>({caseId,courtName,caseType,caseStatus,label}));
    return {viewKind:'case-list', cases, canOpenCaseList};
  }

  function openKurumCaseList() {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    if (detected.sourceSystem !== adapters?.SOURCE_SYSTEM?.KURUM) throw new Error('UYAP_KURUM_PORTAL_NOT_ACTIVE: Aktif ekran UYAP Kurum değil.');
    const actions = adapters.kurumCaseListActions?.(document, visible) || [];
    if (actions.length !== 1) throw new Error(`UYAP_KURUM_CASE_LIST_ACTION_AMBIGUOUS: “Dosya Raporlama” için tek ve güvenli menü eylemi bulunamadı. Eşleşme: ${actions.length}.`);
    const action = actions[0];
    action.scrollIntoView?.({block:'center',inline:'nearest'});
    if (!dispatchClick(action)) throw new Error('UYAP_KURUM_CASE_LIST_OPEN_FAILED: “Dosya Raporlama” menüsü açılamadı.');
    return {opened:true};
  }

  function openKurumCase(targetCaseId) {
    if (adapters.kurumViewKind?.(document, location) !== 'case-list') throw new Error('UYAP_KURUM_CASE_LIST_NOT_ACTIVE: UYAP Kurum dosya listesi açık değil.');
    const result=adapters.kurumCaseTarget?.(document, visible, targetCaseId);
    if (!result?.target) throw new Error(`UYAP_KURUM_CASE_SELECTION_AMBIGUOUS: ${targetCaseId || 'Dosya'} için tek ve güvenli satır bulunamadı. Eşleşme: ${result?.matchCount || 0}, görünür dosya: ${result?.candidateCount || 0}.`);
    const {row,action,caseId,courtName}=result.target;
    if (!row || !action || !visible(row) || !visible(action)) throw new Error('UYAP_KURUM_CASE_OPEN_UNAVAILABLE: Dosya Görüntüle eylemi görünür değil.');
    rememberKurumSelection({caseId,courtName});
    row.scrollIntoView?.({block:'center',inline:'nearest'});
    if (!dispatchClick(action)) throw new Error('UYAP_KURUM_CASE_OPEN_FAILED: Dosya Görüntüle eylemi tetiklenemedi.');
    return {opened:true,caseId};
  }

  async function openKurumCaseSmart({caseId,courtName,openContext} = {}) {
    const normalizedCaseId = String(caseId || '').replace(/\s+/g,'').replace('-', '/');
    if (!/^20\d{2}\/\d{1,10}$/.test(normalizedCaseId)) throw new Error('UYAP_KURUM_SMART_OPEN_INVALID_CASE: Geçerli dosya numarası yok.');
    const requestId = `open-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const payload = {requestId,caseId:normalizedCaseId,courtName:normalizedText(courtName || 'UYAP Kurum'),openContext:openContext || null};
    document.documentElement?.setAttribute(KURUM_OPEN_REQUEST_ATTR, JSON.stringify(payload));
    document.dispatchEvent(new Event(KURUM_OPEN_REQUEST_EVENT));
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      try {
        const raw = document.documentElement?.getAttribute(KURUM_OPEN_RESPONSE_ATTR);
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed?.requestId === requestId) {
            if (!parsed.ok) throw new Error(parsed.error || 'UYAP Kurum dosyası otomatik açılamadı.');
            return {opened:true,caseId:normalizedCaseId,mode:parsed.mode || 'smart'};
          }
        }
      } catch (error) {
        if (!/Unexpected end|JSON/i.test(String(error))) throw error;
      }
      await sleep(100);
    }
    throw new Error(`UYAP_KURUM_SMART_OPEN_TIMEOUT: ${normalizedCaseId} otomatik açma isteği zaman aşımına uğradı.`);
  }

  async function revealFile(fileElement) {
    const ancestors = [];
    let listItem = fileElement.closest("li")?.parentElement?.closest("li") || null;
    while (listItem) {
      ancestors.unshift(listItem);
      listItem = listItem.parentElement?.closest("li") || null;
    }
    for (const ancestor of ancestors) {
      const childList = directChild(ancestor, "ul");
      if (childList && !visible(childList)) {
        const toggle = directChild(ancestor, "div.hitarea");
        toggle?.click();
        await sleep(80);
      }
    }
  }

  async function waitForSaveMenu(timeoutMs = 5_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const candidates = [...document.querySelectorAll(profile.saveMenuSelector)];
      const save = candidates.find((element) => normalizedText(element.textContent) === "Kaydet" && visible(element));
      if (save) return save;
      await sleep(50);
    }
    return null;
  }

  function visibleSaveMenuItems() {
    return [...document.querySelectorAll(profile.saveMenuSelector)]
      .filter((element) => normalizedText(element.textContent) === "Kaydet" && visible(element));
  }

  async function dismissStaleSaveMenuIfNeeded() {
    if (!visibleSaveMenuItems().length) return true;
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
    document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));
    const deadline = Date.now() + 120;
    while (Date.now() < deadline) {
      if (!visibleSaveMenuItems().length) return true;
      await sleep(20);
    }
    return !visibleSaveMenuItems().length;
  }

  function kurumRowSelectionControl(row) {
    if (!row?.querySelector) return null;
    return row.querySelector([
      'input[type="checkbox"]', 'input[type="radio"]', '[role="checkbox"]', '[role="radio"]',
      '.ag-selection-checkbox', '.x-grid-row-checker', '.dx-select-checkbox', '.ui-chkbox-box'
    ].join(', '));
  }

  function kurumToolbarActionSignature(element) {
    if (!element) return '';
    const attrs = ['title','aria-label','data-action','data-command','href','onclick','class']
      .map((name) => `${name}=${normalizedText(element.getAttribute?.(name) || '')}`)
      .join('|');
    return `${attrs}|text=${normalizedText(element.textContent || '').slice(0,160)}`;
  }

  async function prepareKurumGlobalToolbarTarget(target, timeoutMs = 2_500) {
    if (!target?.row) return null;
    const beforeActions = adapters.globalDownloadActions(document, visible, {allowDisabled:true});
    const beforeState = new Map(beforeActions.map((action) => [action, {
      disabled:Boolean(adapters.isDisabled(action)),
      signature:kurumToolbarActionSignature(action)
    }]));
    const selector = kurumRowSelectionControl(target.row);
    const selectionTarget = selector || target.row;
    if (!dispatchClick(selectionTarget)) return null;
    const deadline = Date.now() + Math.max(500, timeoutMs);
    while (Date.now() < deadline) {
      const currentActions = adapters.globalDownloadActions(document, visible, {allowDisabled:true});
      const transitioned = currentActions.filter((action) => {
        if (adapters.isDisabled(action)) return false;
        const previous = beforeState.get(action);
        if (!previous) return true;
        if (previous.disabled) return true;
        return previous.signature !== kurumToolbarActionSignature(action);
      });
      if (transitioned.length === 1) {
        return {...target, action:transitioned[0], actionMode:'global-toolbar'};
      }
      // If more than one command becomes active, the selected row cannot be
      // bound to a unique download action safely. Keep waiting briefly in case
      // Kurum is still updating the toolbar, then fail closed.
      await sleep(80);
    }
    return null;
  }

  function parsedIdentityEvidence(value) {
    if (Array.isArray(value)) return value;
    try {
      const parsed = JSON.parse(String(value || ''));
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function classicCandidateLooksRelevant(fileElement, evidence = {}) {
    if (!fileElement) return false;
    const identityParts = parsedIdentityEvidence(evidence.identityEvidence);
    const attachmentParentId = normalizedText(fileElement.getAttribute('ana_evrak_id'));
    const isAttachment = Boolean(attachmentParentId);
    if (typeof evidence.isAttachment === 'boolean' && evidence.isAttachment !== isAttachment) return false;
    const ownTitle = normalizedText(fileElement.closest('li')?.dataset?.sid || fileElement.textContent) || 'Adsiz Evrak';

    if (isAttachment) {
      const expectedParent = normalizedText(identityParts[0]);
      const expectedOwnTitle = normalizedText(identityParts[1]);
      if (expectedParent && attachmentParentId !== expectedParent) return false;
      if (expectedOwnTitle && ownTitle !== expectedOwnTitle) return false;
      return true;
    }

    const expectedOwnTitle = normalizedText(identityParts[0]);
    if (expectedOwnTitle && ownTitle !== expectedOwnTitle) return false;
    const expectedUnit = normalizedText(evidence.unitDocumentNumber || identityParts[1]);
    const expectedApproval = normalizedText(identityParts[2]);
    const expectedSender = normalizedText(evidence.sender || identityParts[3]);
    const expectedType = normalizedText(evidence.documentType || identityParts[4]);
    if (!(expectedUnit || expectedApproval || expectedSender || expectedType)) return true;
    const metadata = parseMetadata(fileElement);
    if (expectedUnit && normalizedText(metadata.unitDocumentNumber) !== expectedUnit) return false;
    if (expectedApproval && normalizedText(metadata.approvalDate) !== expectedApproval) return false;
    if (expectedSender && normalizedText(metadata.sender) !== expectedSender) return false;
    if (expectedType && normalizedText(metadata.documentType) !== expectedType) return false;
    return true;
  }

  async function locateClassicDocumentByEvidence(documentId, evidence = {}, detected = null) {
    const roots = [...document.querySelectorAll('ul.treeview')].filter(visible)
      .filter(root => !root.parentElement?.closest('ul.treeview'));
    if (roots.length > 1) throw new Error('REBIND_AMBIGUOUS_TREE: Birden çok görünür evrak ağacı var.');
    const treeRoot = roots[0] || document;
    const isKurumClassic = detected?.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM;
    const identity = isKurumClassic ? effectiveKurumIdentity() : pageIdentity();
    let root = treeRoot;
    if (isKurumClassic && treeRoot !== document) {
      const activeCaseId = normalizedText(identity.caseId).replace(/\s+/g, '');
      const scoped = /^20\d{2}\/\d{1,10}$/.test(activeCaseId) ? kurumClassicCaseScope(treeRoot, activeCaseId) : null;
      if (!scoped) throw new Error('REBIND_KURUM_CASE_SCOPE_NOT_FOUND: Aktif Kurum dava dalı doğrulanamadı.');
      root = scoped;
    }

    const sourceInfo = detected?.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM
      ? {
          ...detected,
          institutionName: identity.courtName,
          sourceContext: {
            adapter: 'uyap-kurum-classic-tree-fallback-v1',
            pageUrl: location.origin + location.pathname,
            institutionName: identity.courtName,
            caseId: identity.caseId
          }
        }
      : detected;
    const expectedSourceId = String(evidence.sourceDocumentId || '').trim();
    const expectedIdentityHash = String(evidence.sourceIdentityHash || '').trim();
    const candidates = [...root.querySelectorAll(profile.exactFileSelector)]
      .filter((element) => classicCandidateLooksRelevant(element, evidence));

    // Narrow by cheap metadata first, then build the canonical identity only
    // for plausible rows. This is intentionally per-document: unlike a full
    // scan it does not allocate/build 10k+ records or rewrite the whole IDB
    // session when one retry loses its runtime DOM marker.
    for (let index = 0; index < candidates.length; index += 1) {
      const element = candidates[index];
      const record = await buildRecord(element, index, identity, sourceInfo);
      if (expectedSourceId && record.sourceDocumentId && String(record.sourceDocumentId) !== expectedSourceId) continue;
      if (record.documentId !== documentId && !(expectedSourceId && String(record.sourceDocumentId || '') === expectedSourceId)) continue;
      // Encrypted classic-tree tokens can rotate across page/session refreshes.
      // The canonical documentId remains authoritative for main documents.
      // Attachments need the token hash as an additional discriminator because
      // parent+label can legitimately repeat and the initial scan already
      // treats that situation as ambiguous/fail-closed.
      if (Boolean(evidence.isAttachment) && expectedIdentityHash && record.sourceIdentityHash !== expectedIdentityHash) continue;
      element.dataset.uyapArchiveDocumentId = documentId;
      return {ok:true, runtimeDocumentId:documentId, method:'targeted-classic-identity'};
    }
    throw new Error('REBIND_DOCUMENT_NOT_FOUND: Evrak hedefli kimlik kanıtıyla bulunamadı.');
  }

  async function rebindDocument(documentId, options = {}) {
    const direct = document.querySelector(`[data-uyap-archive-document-id="${CSS.escape(documentId)}"]`);
    if (direct) return {ok:true, runtimeDocumentId:documentId, method:'existing-runtime-marker'};

    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    const kurumViewKind = detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM
      ? (adapters.kurumViewKind?.(document, location) || 'unknown')
      : null;
    const isKurumClassicTree = kurumViewKind === 'classic-tree';
    if (detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM && !isKurumClassicTree) {
      const target = await locateKurumTarget(documentId, options.kurumLocator || null);
      if (target?.row) {
        target.row.dataset.uyapArchiveDocumentId = documentId;
        if (target.action) target.action.dataset.uyapArchiveKurumActionFor = documentId;
        return {ok:true, runtimeDocumentId:documentId, method:'targeted-kurum-locator'};
      }
      throw new Error('REBIND_KURUM_DOCUMENT_NOT_FOUND: Kurum evrakı hedefli locator ile bulunamadı.');
    }
    return locateClassicDocumentByEvidence(documentId, options.evidence || {}, detected);
  }

  async function preflightDocument(documentId, options = {}) {
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    const kurumViewKind = detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM
      ? (adapters.kurumViewKind?.(document, location) || 'unknown')
      : null;
    const isKurumClassicTree = kurumViewKind === 'classic-tree';
    if ((options.kurumLocator || detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM) && !isKurumClassicTree) {
      let target = await locateKurumTarget(documentId, options.kurumLocator || null);
      if (target?.action && target?.row) {
        if (!visible(target.row)) throw new Error('PREFLIGHT_KURUM_ROW_HIDDEN: UYAP Kurum evrak satırı görünür değil.');
        target.row.scrollIntoView?.({ block:'center', inline:'nearest' });
        if (String(target.actionMode || '').startsWith('global-toolbar')) {
          const prepared = await prepareKurumGlobalToolbarTarget(target);
          if (!prepared?.action) throw new Error('PREFLIGHT_KURUM_GLOBAL_DOWNLOAD_DISABLED: Satır seçildi fakat Kurum İndir/Kaydet komutu etkinleşmedi.');
          target = prepared;
        }
        if (!visible(target.action) || adapters.isDisabled?.(target.action)) throw new Error('PREFLIGHT_KURUM_ACTION_UNAVAILABLE: UYAP Kurum indirme eylemi görünür/etkin değil.');
        target.action.scrollIntoView?.({ block:'center', inline:'nearest' });
        return {ok:true, method:String(target.actionMode || '').startsWith('global-toolbar') ? 'kurum-global-toolbar' : 'kurum-action', documentId};
      }
      if (options.kurumLocator) throw new Error('PREFLIGHT_KURUM_DOCUMENT_NOT_FOUND: UYAP Kurum evrakı sayfa/grid içinde yeniden bulunamadı.');
    }

    const selector = `[data-uyap-archive-document-id="${CSS.escape(documentId)}"]`;
    let matches = [...document.querySelectorAll(selector)];
    if (!matches.length && options.allowFullScan !== false) {
      await scanPage(true);
      matches = [...document.querySelectorAll(selector)];
    }
    const fileElement = matches.find(visible) || matches[0];
    if (!fileElement) throw new Error("PREFLIGHT_DOCUMENT_NOT_FOUND: Evrak bu çerçevede bulunamadı.");

    const kurumAction = document.querySelector(`[data-uyap-archive-kurum-action-for="${CSS.escape(documentId)}"]`);
    if (kurumAction) {
      if (!visible(kurumAction)) throw new Error('PREFLIGHT_KURUM_ACTION_HIDDEN: UYAP Kurum indirme eylemi görünür değil.');
      kurumAction.scrollIntoView?.({ block: 'center', inline: 'nearest' });
      return {ok:true, method:'kurum-action', documentId};
    }

    await revealFile(fileElement);
    if (!visible(fileElement)) throw new Error('PREFLIGHT_ROW_HIDDEN: Evrak satırı görünür hale gelmedi.');

    // UYAP Kurum'un legacy jQuery Treeview ekranında başlangıç güvenlik
    // kontrolü bağlam menüsünü programatik olarak açmamalıdır. Chrome/UYAP
    // sürümleri sentetik contextmenu olaylarını farklı işleyebilir; bu durum
    // kaynak kimliği doğru olduğu halde tüm kuyruğu START_PATH_DEGRADED
    // durumuna sokuyordu. Classic-tree için burada yalnızca deterministik
    // runtime bağını doğrularız. Gerçek Kaydet eylemi ilk gerçek kuyruk
    // denemesinde çalıştırılır ve üç yapısal başlangıç hatasından sonra mevcut
    // circuit-breaker güvenli recovery'ye geçer.
    if (isKurumClassicTree) {
      const token = normalizedText(fileElement.getAttribute('evrak_id')).replace(/^"|"$/g, '');
      if (!token) throw new Error('PREFLIGHT_KURUM_CLASSIC_TOKEN_MISSING: Evrak düğümünde evrak_id bulunamadı.');
      const treeRoot = fileElement.closest('ul.treeview') || document.querySelector('ul#browser, ul.treeview');
      const identity = effectiveKurumIdentity();
      const scoped = treeRoot && /^20\d{2}\/\d{1,10}$/.test(normalizedText(identity.caseId))
        ? kurumClassicCaseScope(treeRoot, identity.caseId)
        : null;
      if (!scoped || !scoped.contains(fileElement)) {
        throw new Error(`PREFLIGHT_KURUM_CLASSIC_SCOPE_MISMATCH: ${identity.caseId || 'Açık dosya'} için runtime evrak aktif dava dalında doğrulanamadı.`);
      }
      return {ok:true, method:'kurum-classic-tree-binding', documentId};
    }

    if (!(await dismissStaleSaveMenuIfNeeded())) {
      throw new Error('PREFLIGHT_STALE_SAVE_MENU: Önceki Kaydet menüsü kapanmadı.');
    }

    fileElement.scrollIntoView({ block: "center", inline: "nearest" });
    const rectangle = fileElement.getBoundingClientRect();
    fileElement.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,cancelable: true,button: 2,buttons: 2,
      clientX: rectangle.left + Math.min(12, rectangle.width / 2),
      clientY: rectangle.top + Math.min(10, rectangle.height / 2),view: window
    }));

    const timeoutMs = Math.min(7_500, Math.max(1_500, Number(options.saveMenuTimeoutMs || 5_000)));
    const saveInner = await waitForSaveMenu(timeoutMs);
    if (!saveInner) throw new Error('PREFLIGHT_SAVE_MENU_NOT_OPENED: UYAP bağlam menüsündeki Kaydet komutu açılamadı.');
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
    document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));
    await sleep(80);
    return {ok:true, method:'classic-context-menu', documentId};
  }

  async function clickDocument(documentId, attemptId, options = {}) {
    if (!/^[a-zA-Z0-9-]{1,80}$/.test(attemptId || '')) throw new Error('INVALID_ATTEMPT_ID');
    const detected = adapters?.detect?.(document, location) || {sourceSystem:'UYAP'};
    const kurumViewKind = detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM
      ? (adapters.kurumViewKind?.(document, location) || 'unknown')
      : null;
    const isKurumClassicTree = kurumViewKind === 'classic-tree';
    if ((options.kurumLocator || detected.sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM) && !isKurumClassicTree) {
      let target = await locateKurumTarget(documentId, options.kurumLocator || null);
      if (target?.row && target?.action) {
        if (!visible(target.row)) throw new Error('UYAP Kurum evrak satırı görünür değil.');
        target.row.scrollIntoView?.({ block:'center', inline:'nearest' });
        if (String(target.actionMode || '').startsWith('global-toolbar')) {
          // Some UYAP Kurum grids expose a disabled toolbar action until the row
          // checkbox/selection is activated. Select safely, then wait for the
          // exact single download command to become enabled.
          const prepared = await prepareKurumGlobalToolbarTarget(target);
          if (!prepared?.action) throw new Error('UYAP_KURUM_GLOBAL_DOWNLOAD_NOT_FOUND: Satır seçildi fakat global İndir/Kaydet komutu etkinleşmedi.');
          target = prepared;
        }
        if (!visible(target.action) || adapters.isDisabled?.(target.action)) throw new Error('UYAP Kurum indirme eylemi görünür/etkin değil.');
        target.action.scrollIntoView?.({ block:'center', inline:'nearest' });
        target.action.setAttribute('data-uyap-archive-kurum-action-for', documentId);
        target.action.setAttribute('data-uyap-archive-save-attempt', attemptId);
        const captured = await chrome.runtime.sendMessage({
          type:'capture_save', attemptId, documentId,
          captureWindowMs:Math.min(2_500,Math.max(180,Number(options.captureWindowMs || 550)))
        });
        if (!captured?.ok) throw new Error(captured?.error || 'SAVE_CAPTURE_FAILED');
        return {
          ok:true,
          request:captured.data?.request || null,
          captureStatus:captured.data?.captureStatus || 'NATIVE_DOWNLOAD_EXPECTED'
        };
      }
      if (options.kurumLocator) throw new Error('UYAP_KURUM_DOCUMENT_NOT_FOUND: Evrak sayfa/grid içinde yeniden bulunamadı.');
    }

    const selector = `[data-uyap-archive-document-id="${CSS.escape(documentId)}"]`;
    let matches = [...document.querySelectorAll(selector)];
    if (!matches.length) {
      await scanPage(true);
      matches = [...document.querySelectorAll(selector)];
    }
    const fileElement = matches.find(visible) || matches[0];
    if (!fileElement) throw new Error("Evrak bu çerçevede bulunamadı.");
    const kurumAction = document.querySelector(`[data-uyap-archive-kurum-action-for="${CSS.escape(documentId)}"]`);
    if (kurumAction) {
      if (!visible(kurumAction)) throw new Error('UYAP Kurum indirme eylemi görünür değil.');
      kurumAction.scrollIntoView?.({ block: 'center', inline: 'nearest' });
      kurumAction.setAttribute('data-uyap-archive-save-attempt', attemptId);
      const captured = await chrome.runtime.sendMessage({type:'capture_save', attemptId, documentId, captureWindowMs:Math.min(2_500,Math.max(180,Number(options.captureWindowMs || 550)))});
      if (!captured?.ok) throw new Error(captured?.error || 'SAVE_CAPTURE_FAILED');
      return {ok:true, request:captured.data?.request || null, captureStatus:captured.data?.captureStatus || 'NATIVE_DOWNLOAD_EXPECTED'};
    }
    await revealFile(fileElement);
    if (!visible(fileElement)) throw new Error('Evrak satırı görünür hale gelmedi.');
    if (!(await dismissStaleSaveMenuIfNeeded())) {
      throw new Error('STALE_SAVE_MENU: Önceki Kaydet menüsü kapanmadı.');
    }
    fileElement.scrollIntoView({ block: "center", inline: "nearest" });
    const rectangle = fileElement.getBoundingClientRect();
    fileElement.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,cancelable: true,button: 2,buttons: 2,
      clientX: rectangle.left + Math.min(12, rectangle.width / 2),
      clientY: rectangle.top + Math.min(10, rectangle.height / 2),view: window
    }));
    const saveInner = await waitForSaveMenu(Math.min(5_000, Math.max(1_500, Number(options.saveMenuTimeoutMs || 5_000))));
    if (!saveInner) throw new Error("UYAP bağlam menüsündeki Kaydet komutu açılamadı.");
    saveInner.setAttribute('data-uyap-archive-save-attempt', attemptId);
    const captured = await chrome.runtime.sendMessage({type:'capture_save',attemptId,documentId,captureWindowMs:Math.min(2_500,Math.max(180,Number(options.captureWindowMs || 550)))});
    if (!captured?.ok) throw new Error(captured?.error || 'SAVE_CAPTURE_FAILED');
    return {ok:true, request:captured.data?.request || null, captureStatus:captured.data?.captureStatus || 'NATIVE_DOWNLOAD_EXPECTED'};
  }

  function domHeartbeatEvidence() {
    // A healthy live UYAP DOM is stronger and much cheaper evidence than
    // re-fetching the whole application document. This keeps normal heartbeats
    // and recovery probes in-memory while preserving the network probe as a
    // fallback when the DOM cannot prove the application is alive.
    try {
      const detected = adapters?.detect?.(document, location);
      const sourceSystem = detected?.sourceSystem || null;
      const isKurum = sourceSystem === adapters?.SOURCE_SYSTEM?.KURUM;
      const isClassic = sourceSystem === adapters?.SOURCE_SYSTEM?.CLASSIC || sourceSystem === 'UYAP';

      if (isKurum) {
        const viewKind = adapters.kurumViewKind?.(document, location) || 'unknown';
        const visibleModal = [...document.querySelectorAll('.modal-content, #modal-content')].find(visible);
        const hasTree = [...document.querySelectorAll('ul.treeview')].some(visible);
        const hasCaseList = Boolean(document.querySelector('#dosya_listesi_table, .dataTables_wrapper'));
        const strong = Boolean(visibleModal || hasTree || hasCaseList || ['case-list','classic-tree'].includes(viewKind));
        if (strong) {
          return {
            sourceSystem,
            viewKind,
            title: normalizedText(document.title).slice(0, 300),
            responseUrl: location.href
          };
        }
      }

      if (isClassic) {
        const hasTree = [...document.querySelectorAll('ul.treeview')].some(visible);
        const hasFile = Boolean(document.querySelector(profile.exactFileSelector));
        if (hasTree || hasFile) {
          return {
            sourceSystem: sourceSystem || 'UYAP',
            viewKind: 'classic-tree',
            title: normalizedText(document.title).slice(0, 300),
            responseUrl: location.href
          };
        }
      }
    } catch {}
    return null;
  }

  async function sessionHeartbeat(timeoutMs) {
    const liveEvidence = sessionExpiryEvidenceFromDom({includeBodyFallback:true});
    if (liveEvidence) {
      return {
        ok: true,
        httpOk: true,
        status: 200,
        responseUrl: liveEvidence.url || location.href,
        title: liveEvidence.title || normalizedText(document.title).slice(0, 300),
        visibleText: liveEvidence.text,
        liveDomSignal: true,
        sessionExpiredSignal: true
      };
    }

    const domHealth = domHeartbeatEvidence();
    if (domHealth) {
      return {
        ok: true,
        httpOk: true,
        status: 200,
        responseUrl: domHealth.responseUrl,
        title: domHealth.title,
        visibleText: '',
        liveDomSignal: true,
        domHealth: true,
        sourceSystem: domHealth.sourceSystem,
        viewKind: domHealth.viewKind
      };
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs || 15_000);
    try {
      const response = await fetch(location.href, {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        redirect: "follow",
        signal: controller.signal
      });
      const contentType = response.headers.get("content-type") || "";
      let title = "";
      let visibleText = "";
      if (contentType.includes("text/html")) {
        const html = await response.text();
        const parsed = new DOMParser().parseFromString(html, "text/html");
        parsed.querySelectorAll("script, style, noscript, template").forEach((element) => element.remove());
        title = normalizedText(parsed.title).slice(0, 300);
        visibleText = normalizedText(parsed.body?.textContent).slice(0, 8_000);
      }
      return {
        ok: true,
        httpOk: response.ok,
        status: response.status,
        responseUrl: response.url,
        title,
        visibleText,
        networkFallback: true
      };
    } catch (error) {
      return { ok: false, httpOk: false, error: String(error), networkFallback: true };
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // The live UYAP timeout warning can appear before a navigation or redirect.
  // Observe it proactively so the queue freezes within milliseconds instead of
  // waiting for the next 3-minute heartbeat.
  if (typeof MutationObserver === "function" && document.documentElement) {
    const sessionExpiryObserver = new MutationObserver(() => scheduleSessionExpiredCheck());
    sessionExpiryObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "aria-hidden"]
    });
    scheduleSessionExpiredCheck(50);
  }
  // Logout/login DOM can become visible without a mutation observable in this
  // frame (tab restore, BFCache, focus after an idle redirect). Recheck at user
  // visibility boundaries; this is cheap and avoids a permanent missing badge.
  addEventListener('pageshow', () => scheduleSessionExpiredCheck(50), {passive:true});
  addEventListener('focus', () => scheduleSessionExpiredCheck(100), {passive:true});
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) scheduleSessionExpiredCheck(100);
  }, {passive:true});

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.type === 'archive_probe') {
      sendResponse({ok:true,version:VERSION});
      return false;
    }
    if (request.type === MESSAGE.PROBE_PAGE) {
      try { sendResponse({ok:true, data:probePage()}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.SOURCE_DIAGNOSTIC) {
      try { sendResponse({ok:true, data:sourceDiagnostic()}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.FORMAT_CENSUS) {
      try { sendResponse({ok:true, data:formatCensus()}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.LIST_KURUM_CASES) {
      try { sendResponse({ok:true, data:listKurumCases()}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.OPEN_KURUM_CASE) {
      try { sendResponse({ok:true, data:openKurumCase(request.caseId)}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.OPEN_KURUM_CASE_SMART) {
      openKurumCaseSmart({caseId:request.caseId,courtName:request.courtName,openContext:request.openContext || null})
        .then((data) => sendResponse({ok:true,data}))
        .catch((error) => sendResponse({ok:false,error:String(error)}));
      return true;
    }
    if (request.type === MESSAGE.OPEN_KURUM_CASE_LIST) {
      try { sendResponse({ok:true, data:openKurumCaseList()}); }
      catch (error) { sendResponse({ok:false, error:String(error)}); }
      return false;
    }
    if (request.type === MESSAGE.SCAN_PAGE) {
      scanPage(!request.quick).then((data) => sendResponse({ ok: true, data })).catch((error) => sendResponse({ ok: false, error: String(error) }));
      return true;
    }
    if (request.type === MESSAGE.PREFLIGHT_DOCUMENT) {
      preflightDocument(request.documentId, {saveMenuTimeoutMs:request.saveMenuTimeoutMs,kurumLocator:request.kurumLocator || null}).then(sendResponse).catch((error) => sendResponse({ ok: false, error: String(error) }));
      return true;
    }
    if (request.type === MESSAGE.REBIND_DOCUMENT) {
      rebindDocument(request.documentId, {
        kurumLocator:request.kurumLocator || null,
        evidence:request.evidence || null
      }).then(sendResponse).catch((error) => sendResponse({ok:false,error:String(error)}));
      return true;
    }
    if (request.type === MESSAGE.CLICK_DOCUMENT) {
      clickDocument(request.documentId, request.attemptId, {saveMenuTimeoutMs:request.saveMenuTimeoutMs,captureWindowMs:request.captureWindowMs,kurumLocator:request.kurumLocator || null}).then(sendResponse).catch((error) => sendResponse({ ok: false, error: String(error) }));
      return true;
    }
    if (request.type === MESSAGE.SESSION_HEARTBEAT) {
      sessionHeartbeat(request.timeoutMs).then(sendResponse).catch((error) => sendResponse({ ok: false, error: String(error) }));
      return true;
    }
    return false;
  });
})();
