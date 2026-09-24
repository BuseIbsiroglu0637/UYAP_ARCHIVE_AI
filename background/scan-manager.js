import {createSession, startSession, retryFailed, getSessionSummary, refreshSessionContext, suspendActiveSessionForNewJob, restoreActiveSessionAfterFailedNewJob, compareSessionInventories, resumeExistingSessionForInventory, getCachedKurumCaseOpenContext} from './download-manager.js';
import {sendToFrame, uyapFrames} from './frame-client.js';
let operationTask = null;

function inventorySourceKey(document) {
  const sourceId = String(document?.sourceDocumentId || '').trim();
  if (!sourceId) return null;
  return [
    document?.sourceSystem || 'UYAP',
    document?.institutionName || document?.courtName || '',
    document?.caseId || '',
    sourceId
  ].join('|');
}

function mergeFrameInventories(successful) {
  const uniqueByDocument = new Map();
  const uniqueBySource = new Map();
  let rawDocuments = 0;
  let duplicateDocuments = 0;
  let duplicateSourceIds = 0;
  const frameCounts = [];

  for (const result of successful) {
    const documents = Array.isArray(result?.documents) ? result.documents : [];
    frameCounts.push({frameId:result.frameId, count:documents.length});
    for (const raw of documents) {
      rawDocuments += 1;
      const document = {...raw, frameId:result.frameId};
      const sourceKey = inventorySourceKey(document);
      if (sourceKey && uniqueBySource.has(sourceKey)) {
        duplicateSourceIds += 1;
        continue;
      }
      const existing = uniqueByDocument.get(document.documentId);
      if (existing) {
        duplicateDocuments += 1;
        const incompatible =
          String(existing.sourceDocumentId || '') !== String(document.sourceDocumentId || '') ||
          String(existing.caseId || '') !== String(document.caseId || '') ||
          String(existing.courtName || existing.institutionName || '') !==
            String(document.courtName || document.institutionName || '');
        if (incompatible) {
          throw new Error('FRAME_INVENTORY_COLLISION: Aynı evrak kimliği farklı kaynaklara bağlandı; tarama güvenlik nedeniyle durduruldu.');
        }
        continue;
      }
      uniqueByDocument.set(document.documentId, document);
      if (sourceKey) uniqueBySource.set(sourceKey, document);
    }
  }

  return {
    documents:[...uniqueByDocument.values()],
    diagnostics:{
      scope:'cross-frame-merge',
      rawDocuments,
      uniqueDocuments:uniqueByDocument.size,
      duplicateDocuments,
      duplicateSourceIds,
      frameCounts
    }
  };
}
function diagnosticsFromError(error) {
  const text = String(error || '').replace(/^(?:Error:\s*)+/, '');
  const marker = 'Tanılama:';
  const index = text.indexOf(marker);
  if (index < 0) return null;
  const raw = text.slice(index + marker.length).trim();
  try { return JSON.parse(raw); } catch { return {raw: raw.slice(0, 4000)}; }
}
export async function getOperation() {
  const {archiveOperation} = await chrome.storage.local.get('archiveOperation');
  if (!operationTask && ['scanning','starting'].includes(archiveOperation?.phase)) {
    const error = {phase:'error',updatedAt:Date.now(),error:'Önceki başlatma kesildi. İndirmeyi başlat ile yeniden deneyin.'};
    await chrome.storage.local.set({archiveOperation:error});
    return error;
  }
  return archiveOperation || null;
}
export async function clearOperation() { await setOperation("done"); }

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function kurumCaseFrameStates(tabId) {
  const frames = await uyapFrames(tabId);
  const results = [];
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type:'list_kurum_cases'});
      if (!response?.ok) continue;
      const data = response?.data || {};
      results.push({
        tabId,
        frameId:frame.frameId,
        viewKind:String(data.viewKind || 'unknown'),
        canOpenCaseList:Boolean(data.canOpenCaseList),
        cases:Array.isArray(data.cases) ? data.cases : []
      });
    } catch {}
  }
  return results;
}

async function waitForKurumCaseList(tabId, timeoutMs=10_000) {
  const deadline = Date.now() + timeoutMs;
  let lastStates = [];
  while (Date.now() < deadline) {
    lastStates = await kurumCaseFrameStates(tabId);
    if (lastStates.some((state) => state.cases.length > 0 || state.viewKind === 'case-list')) return lastStates;
    await sleep(250);
  }
  return lastStates;
}

export async function listKurumCases(tabId) {
  let states = await kurumCaseFrameStates(tabId);
  let caseStates = states.filter((state) => state.cases.length > 0);
  if (!caseStates.length) {
    const launchers = states.filter((state) => state.canOpenCaseList);
    if (launchers.length > 1) {
      throw new Error(`UYAP_KURUM_CASE_LIST_ACTION_AMBIGUOUS: “Dosya Raporlama” için birden fazla güvenli menü adayı bulundu. Eşleşme: ${launchers.length}.`);
    }
    if (launchers.length === 1) {
      const launcher = launchers[0];
      try {
        const response = await sendToFrame(tabId, launcher.frameId, {type:'open_kurum_case_list'});
        if (!response?.ok) throw new Error(response?.error || 'Dosya Raporlama açılamadı.');
      } catch (error) {
        if (!/(message port closed|receiving end does not exist|context invalidated|frame was removed)/i.test(String(error))) throw error;
      }
      states = await waitForKurumCaseList(tabId);
      caseStates = states.filter((state) => state.cases.length > 0);
    }
  }
  const unique = new Map();
  for (const frame of caseStates) {
    for (const item of frame.cases) {
      const caseId = String(item?.caseId || '').trim();
      if (!caseId) continue;
      const key = `${caseId}|${String(item?.courtName || '').trim()}`;
      if (!unique.has(key)) unique.set(key, {...item, frameId:frame.frameId});
    }
  }
  const kinds = new Set(states.map((state) => state.viewKind));
  const viewKind = unique.size ? 'case-list' : kinds.has('portal-shell') ? 'portal-shell' : kinds.has('classic-tree') ? 'classic-tree' : kinds.has('document-list') ? 'document-list' : 'other';
  return {viewKind, cases:[...unique.values()]};
}

async function kurumCaseFrames(tabId) {
  const states = await kurumCaseFrameStates(tabId);
  return states.filter((state) => state.cases.length > 0);
}

async function candidateUyapTabIds(primaryTabId) {
  const ids = [primaryTabId];
  try {
    const tabs = await chrome.tabs.query({url:['https://*.uyap.gov.tr/*','https://uyap.gov.tr/*']});
    for (const tab of tabs || []) if (Number.isInteger(tab.id) && !ids.includes(tab.id)) ids.push(tab.id);
  } catch {}
  return ids;
}

async function waitForKurumDocumentView(primaryTabId, targetCaseId, timeoutMs=12_000) {
  const deadline = Date.now() + timeoutMs;
  let lastEvidence = null;
  while (Date.now() < deadline) {
    const tabIds = await candidateUyapTabIds(primaryTabId);
    for (const tabId of tabIds) {
      let frames=[];
      try { frames = await uyapFrames(tabId); } catch { continue; }
      for (const frame of frames || []) {
        try {
          const response = await sendToFrame(tabId, frame.frameId, {type:'probe_page'});
          if (!response?.ok) continue;
          const data=response.data || {};
          lastEvidence={tabId,frameId:frame.frameId,sourceSystem:data.sourceSystem,caseId:data.caseId,viewKind:data.viewKind,hasDocuments:data.hasDocuments};
          const sameCase = !targetCaseId || String(data.caseId || '') === String(targetCaseId);
          if (data.sourceSystem === 'UYAP_KURUM' && sameCase && data.viewKind !== 'case-list' && data.hasDocuments) {
            return {tabId,frameId:frame.frameId,caseId:data.caseId};
          }
        } catch {}
      }
    }
    await sleep(250);
  }
  throw new Error(`UYAP_KURUM_CASE_OPEN_TIMEOUT: ${targetCaseId} dosyası açıldı ancak gerçek evrak ekranı ${Math.round(timeoutMs/1000)} saniye içinde doğrulanamadı.${lastEvidence ? ` Son durum: ${JSON.stringify(lastEvidence)}` : ''}`);
}

async function smartOpenKurumCase(tabId, session) {
  const frames = await uyapFrames(tabId);
  const preferred = [...(frames || [])].sort((a,b) => Number(a.frameId || 0) - Number(b.frameId || 0));
  let lastError = null;
  for (const frame of preferred) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {
        type:'open_kurum_case_smart',
        caseId:session?.caseId || null,
        courtName:session?.courtName || session?.institutionName || null,
        openContext:session?.sourceContext?.kurumCaseOpenContext || await getCachedKurumCaseOpenContext(session?.caseId) || null
      });
      if (response?.ok) return response.data || {opened:true};
      lastError = response?.error || lastError;
    } catch (error) { lastError = error; }
  }
  throw new Error(String(lastError || 'UYAP Kurum dosyası otomatik açılamadı.').replace(/^(?:Error:\s*)+/,''));
}

export async function openKurumCase(tabId, targetCaseId) {
  const frames = await kurumCaseFrames(tabId);
  const matches=[];
  for (const frame of frames) {
    for (const item of frame.cases) {
      if (String(item?.caseId || '') === String(targetCaseId || '')) matches.push({frame,item});
    }
  }
  if (matches.length !== 1) {
    throw new Error(`UYAP_KURUM_CASE_SELECTION_AMBIGUOUS: ${targetCaseId || 'Dosya'} için tek ve güvenli satır bulunamadı. Eşleşme: ${matches.length}.`);
  }
  const match=matches[0];
  try {
    const response=await sendToFrame(tabId,match.frame.frameId,{type:'open_kurum_case',caseId:targetCaseId});
    if (!response?.ok) throw new Error(response?.error || 'Dosya Görüntüle eylemi çalıştırılamadı.');
  } catch (error) {
    // A full-page navigation may destroy the message port immediately after the
    // click. The click itself is synchronous, so continue to the navigation
    // verification step instead of treating a closed port as a failed click.
    if (!/(message port closed|receiving end does not exist|context invalidated|frame was removed)/i.test(String(error))) throw error;
  }
  return waitForKurumDocumentView(tabId,targetCaseId);
}

async function focusTab(tabId) {
  if (!Number.isInteger(tabId)) return;
  try {
    const tab = await chrome.tabs.update(tabId, {active:true});
    if (Number.isInteger(tab?.windowId)) await chrome.windows.update(tab.windowId, {focused:true});
  } catch {}
}

async function matchingOpenDocumentView(session, preferredTabId = null) {
  const ids = [];
  for (const id of [preferredTabId, session?.tabId]) if (Number.isInteger(id) && !ids.includes(id)) ids.push(id);
  try {
    const tabs = await chrome.tabs.query({url:['https://*.uyap.gov.tr/*','https://uyap.gov.tr/*']});
    for (const tab of tabs || []) if (Number.isInteger(tab.id) && !ids.includes(tab.id)) ids.push(tab.id);
  } catch {}
  for (const tabId of ids) {
    let frames=[];
    try { frames = await uyapFrames(tabId); } catch { continue; }
    for (const frame of frames || []) {
      try {
        const response = await sendToFrame(tabId, frame.frameId, {type:'probe_page'});
        if (!response?.ok) continue;
        const data = response.data || {};
        if ((data.sourceSystem || 'UYAP') !== (session.sourceSystem || 'UYAP')) continue;
        if (String(data.caseId || '') !== String(session.caseId || '')) continue;
        if (!data.hasDocuments) continue;
        return {tabId, frameId:frame.frameId, caseId:data.caseId, viewKind:data.viewKind};
      } catch {}
    }
  }
  return null;
}

async function fallbackUyapTabId(preferredTabId = null, sourceSystem = 'UYAP') {
  for (const id of [preferredTabId]) {
    if (!Number.isInteger(id)) continue;
    try {
      const tab = await chrome.tabs.get(id);
      const url = new URL(tab.url || '');
      if (url.protocol === 'https:' && (url.hostname === 'uyap.gov.tr' || url.hostname.endsWith('.uyap.gov.tr'))) return id;
    } catch {}
  }
  try {
    const tabs = await chrome.tabs.query({url:['https://*.uyap.gov.tr/*','https://uyap.gov.tr/*']});
    const preferred = String(sourceSystem || '').toUpperCase() === 'UYAP_KURUM'
      ? tabs.find((tab) => /kurum\.uyap\.gov\.tr/i.test(String(tab.url || '')))
      : tabs.find((tab) => !/kurum\.uyap\.gov\.tr/i.test(String(tab.url || '')));
    return preferred?.id ?? tabs[0]?.id ?? null;
  } catch { return null; }
}

export function resumeSavedSession({sessionId, tabId=null, autoStart=true} = {}) {
  if (operationTask) return Promise.reject(new Error('Tarama veya başlatma işlemi zaten sürüyor.'));
  operationTask=(async()=>{
    const keepAlive = setInterval(() => void chrome.storage.local.get('archiveOperation'), 20_000);
    let previous = null;
    try {
      const target = await getSessionSummary(sessionId);
      if (!target?.sessionId) throw new Error('Yarım kalan arşiv işi bulunamadı.');
      if (target.status === 'completed' && target.archiveStatus === 'VERIFIED_COMPLETE') throw new Error('Bu arşiv işi zaten tamamlanmış.');
      const active = await getSessionSummary();
      if (active?.sessionId && active.sessionId !== sessionId) previous = await suspendActiveSessionForNewJob();
      await setOperation('resuming',{sessionId,targetCaseId:target.caseId,sourceSystem:target.sourceSystem});

      let opened = await matchingOpenDocumentView(target, tabId);
      let effectiveTabId = opened?.tabId ?? null;
      if (!opened) {
        const baseTabId = await fallbackUyapTabId(tabId, target.sourceSystem);
        if (!Number.isInteger(baseTabId)) throw new Error('Açık UYAP sekmesi bulunamadı. UYAP/UYAP Kurum portalını açıp tekrar deneyin.');
        if (String(target.sourceSystem || '').toUpperCase() === 'UYAP_KURUM') {
          let smartError = null;
          try {
            await smartOpenKurumCase(baseTabId, target);
            opened = await waitForKurumDocumentView(baseTabId, target.caseId, 18_000);
            effectiveTabId = opened.tabId;
          } catch (error) {
            smartError = String(error).replace(/^(?:Error:\s*)+/, '');
          }

          // Legacy fallback for sessions created before direct reopen context was
          // persisted. It still allows an already-visible exact case row to open,
          // but the normal path above performs the query/navigation automatically.
          if (!opened) {
            try {
              const listing = await listKurumCases(baseTabId);
              const matches = (listing?.cases || []).filter((item) => String(item?.caseId || '') === String(target.caseId || ''));
              if (matches.length === 1) {
                opened = await openKurumCase(baseTabId, target.caseId);
                effectiveTabId = opened.tabId;
              }
            } catch {}
          }

          if (!opened) {
            throw new Error(`Yarım iş ${target.caseId || ''} otomatik olarak açılamadı. Kullanıcının tekrar “Dosya Görüntüle” yapması gerekmemeli; otomatik sorgu başarısız oldu.${smartError ? ` Ayrıntı: ${smartError}` : ''}`);
          }
        } else {
          throw new Error(`Yarım kalan ${target.caseId || ''} dosyasının Evrak ekranı açık değil. Normal UYAP'ta ilgili dosyayı açtıktan sonra tek tuşla devam edebilirsiniz.`);
        }
      }

      await focusTab(effectiveTabId);
      // Refresh the current source inventory against the exact selected session.
      // This preserves VERIFIED rows, refreshes render-scoped Kurum tokens and
      // adds only genuinely new documents before continuing.
      let resumed = target;
      const activeAfterOpen = await getSessionSummary();
      if (!activeAfterOpen?.sessionId || activeAfterOpen.sessionId !== sessionId) {
        resumed = await scanSession(effectiveTabId,{reuseExisting:true,preferredSessionId:sessionId});
        if (resumed?.sessionId !== sessionId) throw new Error('Yarım iş güvenli biçimde yeniden bağlanamadı; farklı bir oturum seçildi.');
      }
      const result = autoStart === false ? await getSessionSummary(sessionId) : await startSession(sessionId,effectiveTabId);
      await setOperation('done',{sessionId,resultStatus:result?.status||null,resumedSavedSession:true});
      return result;
    } catch(error) {
      if (previous?.sessionId) await restoreActiveSessionAfterFailedNewJob(previous);
      await setOperation('error',{error:String(error).replace(/^(?:Error:\s*)+/,''),resumeSavedSession:true,sessionId});
      throw error;
    } finally { clearInterval(keepAlive); operationTask=null; }
  })();
  return operationTask;
}

export async function getSourceDiagnostic(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const url = new URL(tab.url);
  if (url.protocol !== 'https:' || !(url.hostname === 'uyap.gov.tr' || url.hostname.endsWith('.uyap.gov.tr'))) {
    throw new Error('Aktif sekme UYAP değil.');
  }
  const frames = await uyapFrames(tabId);
  const results = [];
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type:'source_diagnostic'});
      if (!response?.ok) throw new Error(response?.error || 'Çerçeve tanılaması alınamadı.');
      results.push({frameId:frame.frameId, ok:true, ...response.data});
    } catch (error) {
      results.push({frameId:frame.frameId, ok:false, error:String(error).replace(/^(?:Error:\s*)+/, '').slice(0, 500)});
    }
  }
  const successful = results.filter((item) => item.ok);
  if (!successful.length) throw new Error('UYAP ekran tanılaması alınamadı. Sayfayı yenileyip tekrar deneyin.');
  const sourceSystems = [...new Set(successful.map((item) => item.sourceSystem || 'UYAP'))];
  return {
    schemaVersion: 1,
    kind: 'UYAP_SOURCE_DIAGNOSTIC',
    generatedAt: new Date().toISOString(),
    privacy: {
      rawDocumentTitlesIncluded:false,
      rawCaseOrCourtIncluded:false,
      rawUrlsWithQueryIncluded:false,
      rawTokensIncluded:false
    },
    sourceSystems,
    frameCount: results.length,
    successfulFrameCount: successful.length,
    frames: results
  };
}
async function setOperation(phase, extra={}) {
  const previous = await getOperationSnapshot();
  await chrome.storage.local.set({archiveOperation:{...previous,phase,updatedAt:Date.now(),...extra}});
}
async function getOperationSnapshot() {
  const {archiveOperation} = await chrome.storage.local.get('archiveOperation');
  return archiveOperation || {};
}
export async function getFormatCensus(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const url = new URL(tab.url);
  if (url.protocol !== 'https:' || !(url.hostname === 'uyap.gov.tr' || url.hostname.endsWith('.uyap.gov.tr'))) {
    throw new Error('Aktif sekme UYAP değil.');
  }
  const frames = await uyapFrames(tabId);
  const rows = [];
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type:'format_census'});
      if (response?.ok && response.data) rows.push({frameId:frame.frameId,...response.data});
    } catch {}
  }
  if (!rows.length) throw new Error('UYAP format envanteri alınamadı.');
  rows.sort((a,b)=>Number(b.totalNodes||0)-Number(a.totalNodes||0));
  const primary=rows[0];
  return {schemaVersion:1,kind:'UYAP_FORMAT_CENSUS',generatedAt:new Date().toISOString(),primaryFrame:primary,frames:rows,privacy:{rawTitlesIncluded:false,rawTokensIncluded:false,rawUrlsIncluded:false}};
}

export async function scanSession(tabId, {reuseExisting=false, preferredSessionId=null}={}) {
  const tab = await chrome.tabs.get(tabId);
  const url = new URL(tab.url);
  if(url.protocol !== 'https:' || !(url.hostname==='uyap.gov.tr'||url.hostname.endsWith('.uyap.gov.tr'))) throw new Error('Aktif sekme UYAP değil.');
  const current = await getSessionSummary();
  if(current?.status === 'running') throw new Error('Bağlantı kontrolü için önce mevcut işi duraklatın.');
  await setOperation('scanning');
  const frames = await uyapFrames(tabId);
  const results = await Promise.allSettled((frames || []).map(async frame => {
    const result = await sendToFrame(tabId,frame.frameId,{type:'scan_page'});
    if (!result?.ok) throw new Error(result?.error || 'Çerçeve yanıt vermedi');
    return {...result.data,frameId:frame.frameId};
  }));
  const failed = results.filter(r=>r.status==='rejected');
  const critical = failed.find(r=>/envanter|kimli|Klasör|AMBIGUOUS|PAGINATION|ağaç|UYAP_KURUM|STRUCTURE_UNRECOGNIZED|CASE_ID_UNKNOWN|Tanılama/i.test(String(r.reason)));
  if(critical) throw new Error(String(critical.reason));
  const successful=results.filter(r=>r.status==='fulfilled').map(r=>r.value);
  const merged = mergeFrameInventories(successful);
  const documents = merged.documents;
  if(!documents.length) {
    if (current?.sessionId) return refreshSessionContext(current.sessionId, tabId);
    throw new Error('Evrak bulunamadı. UYAP dava dosyasını açıp Evrak sekmesine geçin.');
  }
  if (failed.length) throw new Error('FRAME_SCAN_INCOMPLETE: UYAP çerçevelerinden biri taranamadı. Eksik envanterle iş başlatılmadı.');
  if (current?.sessionId) {
    const first = documents[0];
    if ((first?.sourceSystem || 'UYAP') !== (current.sourceSystem || 'UYAP') || first?.caseId !== current.caseId || first?.courtName !== current.courtName) {
      throw new Error(`Aktif arşiv işi ${current.courtName || "Mahkeme"} ${current.caseId || ""} dosyasına ait. Mevcut sayaçlar korunuyor; farklı dava için ayrı bir "Yeni iş" işlemi kullanılmalıdır.`);
    }
    const session = await refreshSessionContext(current.sessionId, tabId);
    await setOperation('done',{sessionId:session.sessionId,total:session.total,contextChecked:true,
      diagnostics:[...successful.map(r=>({frameId:r.frameId,...r.diagnostics})), merged.diagnostics],failedFrames:failed.length});
    return session;
  }
  const inventoryEvidence=[...successful.map(r=>({frameId:r.frameId,...r.diagnostics})), merged.diagnostics];
  // No active session: always prefer an unfinished matching case before creating
  // a duplicate run. This preserves progress after extension reload/restart.
  if (reuseExisting || !current?.sessionId) {
    const resumed = await resumeExistingSessionForInventory({documents,tabId,inventoryEvidence,preferredSessionId});
    if (resumed) {
      await setOperation('scanned',{
        sessionId:resumed.sessionId,total:resumed.total,resumedExisting:true,
        resumeFromVerified:Number(resumed.resumeFromVerified||0),
        diagnostics:inventoryEvidence,failedFrames:failed.length
      });
      return resumed;
    }
  }
  const session=await createSession({documents,tabId,inventoryEvidence});
  await setOperation('scanned',{sessionId:session.sessionId,total:session.total,
    diagnostics:inventoryEvidence,failedFrames:failed.length,resumedExisting:false});
  return session;
}
export function runOperation({tabId,sessionId=null,start=false,retry=false}) {
  if(operationTask) return Promise.reject(new Error('Tarama veya başlatma işlemi zaten sürüyor.'));
  operationTask=(async()=>{
    const keepAlive = setInterval(() => void chrome.storage.local.get('archiveOperation'), 20_000);
    try {
      let session=sessionId ? await getSessionSummary(sessionId) : null;
      if(!session) session=await scanSession(tabId);
      if(start || retry) {
        await setOperation('starting',{sessionId:session.sessionId});
        session=await (retry ? retryFailed(session.sessionId,tabId) : startSession(session.sessionId,tabId));
      }
      await setOperation('done',{sessionId:session.sessionId});
      return session;
    } catch(error) {
      const diagnostics = diagnosticsFromError(error);
      await setOperation('error',{
        error:String(error).replace(/^(?:Error:\s*)+/,''),
        ...(diagnostics ? {diagnostics} : {})
      });
      throw error;
    } finally { clearInterval(keepAlive); operationTask=null; }
  })();
  return operationTask;
}

export function runNewJob({tabId}) {
  if(operationTask) return Promise.reject(new Error('Tarama veya başlatma işlemi zaten sürüyor.'));
  operationTask=(async()=>{
    const keepAlive = setInterval(() => void chrome.storage.local.get('archiveOperation'), 20_000);
    let previous = null;
    try {
      previous = await suspendActiveSessionForNewJob();
      await setOperation('scanning',{newJob:true,previousSessionId:previous?.sessionId || null});
      let session = await scanSession(tabId,{reuseExisting:true});
      const comparison = previous?.sessionId && previous.sessionId !== session.sessionId
        ? await compareSessionInventories(previous.sessionId, session.sessionId)
        : null;
      if (comparison) session = await getSessionSummary(session.sessionId);
      await setOperation('done',{
        sessionId:session.sessionId,
        newJob:true,
        previousSessionId:previous?.sessionId || null,
        inventoryComparison:comparison,
        resumedExisting:session?.resumedExisting === true,
        resumeFromVerified:Number(session?.resumeFromVerified||0)
      });
      return session;
    } catch(error) {
      if (previous?.sessionId) await restoreActiveSessionAfterFailedNewJob(previous);
      await setOperation('error',{error:String(error).replace(/^(?:Error:\s*)+/,''),newJob:true,restoredSessionId:previous?.sessionId || null});
      throw error;
    } finally { clearInterval(keepAlive); operationTask=null; }
  })();
  return operationTask;
}
