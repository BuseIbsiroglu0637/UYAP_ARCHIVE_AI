const MESSAGE = Object.freeze({
  START_SESSION:"start_session", PAUSE_SESSION:"pause_session", GET_ACTIVE_SESSION:"get_active_session",
  GET_OPERATION:"get_operation", OPEN_LOGIN_ROUTE:"open_login_route", NEW_JOB:"new_job",
  RESET_RUNTIME:"reset_runtime", REPACKAGE_ARCHIVE:"repackage_archive",
  LIST_KURUM_CASES:"list_kurum_cases", OPEN_KURUM_CASE:"open_kurum_case",
  GET_RESUMABLE_SESSIONS:"get_resumable_sessions", RESUME_SAVED_SESSION:"resume_saved_session",
  GET_ARCHIVE_REGISTRY:"get_archive_registry", OPEN_FINAL_PACKAGE_FOLDER:"open_final_package_folder",
  SELECT_EXISTING_ARCHIVE:"select_existing_archive"
});

const $ = (id) => document.getElementById(id);
const el = Object.fromEntries([
  "jobContext","healthBadge","healthText","statusCard","statusEyebrow","statusTitle","message","lastActivity",
  "loginRoutePanel","safeCount","kurumDirectLoginButton","kurumEdevletLoginButton","verified","localExisting","completedCount","total","remainingCount","progressPercent",
  "progressBar","etaLine","speedLine","deferred","review","warnings","deferredCard","reviewCard","attentionBanner",
  "attentionText","primaryButton","workspaceButton","reportButton","rebindButton","newJobButton","supportButton",
  "casePickerPanel","casePickerSelect","casePickerCancel","casePickerOpen",
  "resumeJobsPanel","resumeJobsTitle","resumeJobsCount","resumeJobsList",
  "recentArchivePanel","recentArchiveList","recentArchiveOpen","selectExistingArchiveButton",
  "sourceMode","versionBadge"
].map(id=>[id,$(id)]));

let activeSession=null;
let operation=null;
let busy=false;
let primaryAction="start";
let agentHealth=null;
let lastAgentCheck=0;
let casePickerMode=null;
let casePickerTabId=null;
let resumableSessions=[];
let recentArchiveRegistry=null;
let lastRecentArchiveCheck=0;
const MANIFEST=chrome.runtime?.getManifest?.()||{};
const VERSION=MANIFEST.version_name||MANIFEST.version||"2.9.0 RC15.9.39 AI-FAST-ARCHIVE";
el.versionBadge.textContent=`v${VERSION}`;

async function sendRuntime(type,payload={}){
  const response=await chrome.runtime.sendMessage({type,...payload});
  if(!response?.ok) throw new Error(response?.error||"İşlem başarısız.");
  return response.data;
}

function formatRecentArchiveTime(value){
  const d=new Date(value||0); if(Number.isNaN(d.getTime())) return "-";
  return new Intl.DateTimeFormat("tr-TR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}).format(d);
}
function normalizeArchiveCaseId(value){
  const raw=String(value||"").trim();
  const match=raw.match(/^(20\d{2})[_/-](\d{1,10})$/);
  return match?`${match[1]}/${match[2]}`:raw.replace(/_/g,"/");
}
function archiveCaseDescriptor(d){
  const parts=String(d?.targetPath||"").replace(/\\/g,"/").split("/").filter(Boolean);
  const court=String(d?.courtName||parts[0]||"UYAP Dosyası").trim();
  const caseId=normalizeArchiveCaseId(d?.caseId||String(parts[1]||"").replace(/_(?:Dosya|Esas)$/i,""));
  let title=String(d?.caseTitle||parts[2]||caseId||"Dava Dosyası").trim();
  title=title.replace(/^(20\d{2})_(\d{1,10})(?=\b|\s|\()/,(_,year,no)=>`${year}/${no}`);
  if(caseId&&!title.includes(caseId)&&!/^20\d{2}\/\d+/.test(title)) title=`${caseId} • ${title}`;
  const key=[court.toLocaleLowerCase("tr-TR"),caseId||title.toLocaleLowerCase("tr-TR")].join("|");
  return {key,title,court,caseId};
}
function recentArchiveCaseGroups(limit=20){
  const indexed=Array.isArray(recentArchiveRegistry?.caseIndex)?recentArchiveRegistry.caseIndex:[];
  if(indexed.length){
    return indexed.slice(0,limit).map((g,index)=>({
      key:`${g.institutionName||""}|${g.caseId||index}`,
      title:g.caseTitle||g.caseId||"Dava/İcra Dosyası",
      court:g.institutionName||"Bilinmeyen Kurum/Daire",
      caseId:normalizeArchiveCaseId(g.caseId),
      count:Number(g.documentCount||0),verified:Number(g.verifiedCount||0),
      physical:Number(g.physicalReadyCount??g.presentCount??0),
      metadataVerified:Number(g.metadataVerifiedCount??g.verifiedCount??0),
      problem:Number(g.problemCount||0),missing:Number(g.missingCount||0),
      sizeMismatch:Number(g.sizeMismatchCount||0),staleMetadata:Number(g.staleMetadataCount||0),
      totalBytes:Number(g.totalBytes||0),when:g.latestAt||null,sourceSystem:g.sourceSystem||null
    }));
  }
  const docs=(recentArchiveRegistry?.documents||[]).filter(d=>d?.status==="archived");
  const groups=new Map();
  for(const d of docs){
    const info=archiveCaseDescriptor(d),when=d.fileModifiedAt||d.updatedAt||null;
    let group=groups.get(info.key);
    if(!group){group={...info,when,count:0,verified:0,problem:0,missing:0};groups.set(info.key,group);}
    group.count+=1; group.verified+=1;
    if((new Date(when||0).getTime()||0)>(new Date(group.when||0).getTime()||0)) group.when=when;
  }
  return [...groups.values()].sort((a,b)=>(new Date(b.when||0).getTime()||0)-(new Date(a.when||0).getTime()||0)).slice(0,limit);
}
function renderRecentArchive(){
  const groups=recentArchiveCaseGroups(20);
  el.recentArchiveList.textContent=""; el.recentArchivePanel.hidden=false;
  for(const group of groups){
    const row=document.createElement("div"); row.className="recent-archive-row";
    const institution=document.createElement("div"); institution.className="recent-archive-cell";
    const institutionName=document.createElement("strong"); institutionName.textContent=group.court||"Bilinmeyen Kurum/Daire";
    const status=document.createElement("small");
    if(group.missing||group.sizeMismatch){
      const issues=[]; if(group.missing)issues.push(`${group.missing} eksik`); if(group.sizeMismatch)issues.push(`${group.sizeMismatch} boyut uyuşmaz`);
      status.textContent=`${group.physical} fiziksel uygun • ${issues.join(" • ")}`;
    }else{
      status.textContent=`${group.physical||group.count} fiziksel uygun${group.when?` • ${formatRecentArchiveTime(group.when)}`:""}`;
    }
    institution.append(institutionName,status);
    const caseCell=document.createElement("div"); caseCell.className="recent-archive-case"; caseCell.textContent=group.caseId||"-";
    const count=document.createElement("div"); count.className="recent-archive-count"; count.textContent=Number(group.count||0).toLocaleString("tr-TR");
    row.append(institution,caseCell,count); el.recentArchiveList.append(row);
  }
  const staleCount=Number(recentArchiveRegistry?.staleMetadataFiles||0);
  if(staleCount>0){
    const stale=document.createElement("div"); stale.className="recent-archive-warning";
    stale.textContent=`${staleCount.toLocaleString("tr-TR")} DB kaydı fiziksel dosya olmadan/uyuşmadan bulundu; arşivlenmiş sayılmıyor.`;
    el.recentArchiveList.append(stale);
  }
  const rootNote=document.createElement("div"); rootNote.className="recent-archive-warning";
  rootNote.textContent=`Arşiv kaynağı: ${recentArchiveRegistry?.archiveRoot||recentArchiveRegistry?.downloadsRoot||"bulunamadı"} • İlk ekran boyut/varlık kanıtı kullanır; ALREADY_ARCHIVED için SHA-256 gerekir.`;
  el.recentArchiveList.append(rootNote);
  const warnings=Array.isArray(recentArchiveRegistry?.registryWarnings)?recentArchiveRegistry.registryWarnings:[];
  if(warnings.length){
    const note=document.createElement("div"); note.className="recent-archive-warning";
    note.textContent=`Yerel arşiv indeksi ${warnings.length} veritabanı uyarısıyla kurtarmalı olarak okundu.`;
    el.recentArchiveList.append(note);
  }
}

async function activeUyapTab(){
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  try{
    const url=new URL(tab?.url||"");
    if(url.protocol==="https:"&&(url.hostname==="uyap.gov.tr"||url.hostname.endsWith(".uyap.gov.tr"))) return tab;
  }catch{}
  const tabs=await chrome.tabs.query({url:["https://*.uyap.gov.tr/*","https://uyap.gov.tr/*"]});
  const candidate=tabs.find(t=>Number.isInteger(t.id));
  if(candidate) return candidate;
  throw new Error("UYAP dava/evrak ekranı bulunamadı. Önce UYAP sekmesini açın.");
}

function counts(session){return session?.counts||{};}
function downloadedCount(session){return Number(counts(session).verified||0);}
function userExcludedCount(session){return Number(session?.userExcludedCount||session?.finalIntegrity?.userExcludedCount||0);}
function localExistingCount(session){
  const recognized=Number(session?.localArchiveRecognizedCount||0);
  const skipped=Number(counts(session).skipped||0);
  const localSkipped=Math.max(0,skipped-userExcludedCount(session));
  return Math.max(recognized,localSkipped);
}
function completedCount(session){return Number(counts(session).verified||0)+Number(counts(session).skipped||0);}
function reviewCount(session){return Number(counts(session).review_required||0)+Number(counts(session).failed||0);}
function deferredCount(session){return Number(counts(session).retry_wait||0);}
function formatRate(value){const n=Number(value||0);return n>0?`${n.toFixed(n<10?1:0)}/dk`:"-";}
function formatEta(remaining,rate){
  const r=Number(remaining),p=Number(rate); if(!(r>0)||!(p>0)) return "Kalan süre: -";
  const min=r/p,h=Math.floor(min/60),m=Math.max(1,Math.round(min%60));
  return h>0?`Tahmini kalan: ${h} sa ${m} dk`:`Tahmini kalan: ${m} dk`;
}
function relativeTime(value){
  const ts=Date.parse(value||""); if(!Number.isFinite(ts)) return "Son hareket: -";
  const sec=Math.max(0,Math.round((Date.now()-ts)/1000));
  if(sec<60) return `Son hareket: ${sec} sn önce`; const min=Math.round(sec/60); if(min<60) return `Son hareket: ${min} dk önce`;
  const h=Math.round(min/60); return `Son hareket: ${h} sa önce`;
}
function sourceLabel(session){return session?.sourceSystem==="UYAP_KURUM"?"UYAP Kurum":"UYAP";}
function contextLabel(session){
  if(!session) return "UYAP / UYAP Kurum";
  const source=sourceLabel(session);
  const institution=session.institutionName||session.courtName;
  const parts=[source];
  if(institution&&String(institution).trim().toLocaleLowerCase("tr-TR")!==source.toLocaleLowerCase("tr-TR")) parts.push(institution);
  if(session.caseId) parts.push(session.caseId);
  return parts.join(" • ")||source;
}
function setHealth(label,tone="neutral"){
  el.healthText.textContent=label; el.healthBadge.className=`health-badge ${tone}`;
}
function setStatus(title,message,tone="info",eyebrow="DURUM"){
  el.statusTitle.textContent=title; el.message.textContent=message; el.statusEyebrow.textContent=eyebrow;
  el.statusCard.className=`status-card tone-${tone}`;
}
function userFacingError(error){
  const raw=String(error||"").replace(/^(?:Error:\s*)+/,"");
  if(/UYAP_KURUM_CASE_OPEN_REQUIRED/.test(raw)) return "UYAP Kurum dosya listesindesiniz. Arşivlenecek dosyayı UYAP Archive içinden seçin; ‘Dosya Görüntüle’ adımı otomatik yapılacak.";
  if(/UYAP_KURUM_CASE_LIST_EMPTY/.test(raw)) return "UYAP Kurum bağlantısı hazır. Dosya Raporlama ekranı açıldı ancak listede seçilebilir dosya yok. Gerekirse filtreleri belirleyip ‘Dosya Sorgula’ ile listeyi oluşturun.";
  if(/UYAP_KURUM_PORTAL_READY/.test(raw)) return "UYAP Kurum ana ekranı algılandı. Dosya Raporlama menüsü otomatik açılamadı; menüyü bir kez açtıktan sonra arşivlemeyi tekrar başlatın.";
  if(/UYAP_KURUM_CASE_(?:SELECTION_AMBIGUOUS|OPEN_TIMEOUT|OPEN_UNAVAILABLE|OPEN_FAILED)/.test(raw)) return raw.split(/\s+Son durum:/i)[0].replace(/^UYAP_KURUM_[A-Z_]+:\s*/,"");
  if(/UYAP_KURUM_STRUCTURE_UNRECOGNIZED/.test(raw)) return "UYAP Kurum evrak tablosu güvenli biçimde tanınamadı. İş başlatılmadı ve mevcut arşiv değişmedi. Arşiv Merkezi > Destek bölümünden ekran tanısı oluşturabilirsiniz.";
  if(/UYAP_KURUM_GLOBAL_DOWNLOAD/.test(raw)) return "UYAP Kurum'da evrak satırı seçildi ancak tek bir güvenli indirme komutu doğrulanamadı. İşlem güvenlik için durduruldu.";
  if(/UYAP_KURUM_(?:AMBIGUOUS|PAGINATION|VIRTUAL|SOURCE_SCOPE)/.test(raw)) return "UYAP Kurum evrak yapısında belirsizlik algılandı. Eksik veya yanlış arşiv oluşturmamak için işlem durduruldu.";
  const concise=raw.split(/\s+Tanılama:\s*/i)[0].trim();
  return concise.length>320?`${concise.slice(0,317)}…`:concise;
}
function openWorkspace(view="overview"){
  const q=activeSession?.sessionId?`?session=${encodeURIComponent(activeSession.sessionId)}&view=${encodeURIComponent(view)}`:`?view=${encodeURIComponent(view)}`;
  return chrome.tabs.create({url:chrome.runtime.getURL(`workspace/index.html${q}`)});
}
function openReport(){
  if(!activeSession?.sessionId) return;
  return chrome.tabs.create({url:chrome.runtime.getURL(`report/report.html?session=${encodeURIComponent(activeSession.sessionId)}`)});
}


function hideCasePicker(){
  casePickerMode=null; casePickerTabId=null; el.casePickerPanel.hidden=true; el.casePickerSelect.textContent="";
}
function showCasePicker(cases,mode,tabId){
  casePickerMode=mode; casePickerTabId=tabId; el.casePickerSelect.textContent="";
  for(const item of cases){const option=document.createElement("option");option.value=item.caseId;option.textContent=item.label||[item.courtName,item.caseId,item.caseType,item.caseStatus].filter(Boolean).join(" • ");el.casePickerSelect.append(option);}
  el.casePickerPanel.hidden=false;
  setHealth("Dosya seçimi","good");
  setStatus("Arşivlenecek dosyayı seçin",`${cases.length} UYAP Kurum dosyası bulundu. Doğru dosyayı seçtiğinizde açma ve evrak tarama adımları otomatik yapılacak.`,"info","DOSYA SEÇİMİ");
}
async function offerKurumCaseSelection(mode,tab){
  const listing=await sendRuntime(MESSAGE.LIST_KURUM_CASES,{tabId:tab.id});
  const cases=Array.isArray(listing?.cases)?listing.cases:[];
  if(!cases.length){
    if(listing?.viewKind==="case-list") throw new Error("UYAP_KURUM_CASE_LIST_EMPTY: Dosya Raporlama açık ancak seçilebilir dosya görünmüyor.");
    if(listing?.viewKind==="portal-shell") throw new Error("UYAP_KURUM_PORTAL_READY: UYAP Kurum portalı algılandı ancak Dosya Raporlama listesi henüz hazır değil.");
    return false;
  }
  if(cases.length===1){
    activeSession=await sendRuntime(MESSAGE.OPEN_KURUM_CASE,{tabId:tab.id,caseId:cases[0].caseId,newJob:mode==="newJob"});
    hideCasePicker(); return true;
  }
  showCasePicker(cases,mode,tab.id); return true;
}

function resumableJobLabel(session){
  const institution=session?.institutionName||session?.courtName||"";
  return [session?.caseId,institution].filter(Boolean).join(" • ")||"Yarım arşiv işi";
}
function renderResumableJobs(){
  const activeCompleted=activeSession?.sessionId&&activeSession?.status==="completed"&&activeSession?.archiveStatus==="VERIFIED_COMPLETE";
  const jobs=(resumableSessions||[]).filter((job)=>job?.sessionId&&job.sessionId!==activeSession?.sessionId);
  if(activeCompleted){
    const total=Number(activeSession?.total||0),progress=completedCount(activeSession);
    jobs.unshift({
      sessionId:activeSession.sessionId,caseId:activeSession.caseId,institutionName:activeSession.institutionName,courtName:activeSession.courtName,
      sourceSystem:activeSession.sourceSystem,total,progress,percent:total?Math.min(100,Math.round(progress/total*100)):100,completed:true,current:true
    });
  }
  el.resumeJobsPanel.hidden=jobs.length===0;
  el.resumeJobsCount.textContent=String(jobs.length);
  el.resumeJobsTitle.textContent=jobs.length===1?"1 kayıtlı iş":`${jobs.length} kayıtlı iş`;
  el.resumeJobsList.textContent="";
  for(const job of jobs.slice(0,5)){
    const row=document.createElement("div");row.className=`resume-job-row${job.completed?" completed":""}${job.current?" current":""}`;
    const pct=Number(job.percent||0),progress=Number(job.progress||0),total=Number(job.total||0);
    const stateText=job.completed?(job.current?"Az önce tamamlandı":"Tamamlandı"):"Yarım kaldı";
    const copy=document.createElement("div");copy.className="resume-job-copy";
    const title=document.createElement("strong");title.textContent=resumableJobLabel(job);
    const detail=document.createElement("span");
    detail.append(`${progress.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} • %${pct} • ${sourceLabel(job)} • `);
    const state=document.createElement("b");state.className=`resume-job-state ${job.completed?"good":"warn"}`;state.textContent=stateText;
    detail.append(state);copy.append(title,detail);
    const button=document.createElement("button");button.type="button";button.className=`resume-job-button${job.completed?" completed":""}`;
    button.textContent=job.completed?"Final paketi aç":"Devam et";
    if(job.completed)button.dataset.openPackageSession=String(job.sessionId||"");
    else button.dataset.resumeSession=String(job.sessionId||"");
    row.append(copy,button);
    el.resumeJobsList.append(row);
  }
  el.resumeJobsList.querySelectorAll("[data-resume-session]").forEach((button)=>button.addEventListener("click",()=>void resumeSavedJob(button.dataset.resumeSession)));
  el.resumeJobsList.querySelectorAll("[data-open-package-session]").forEach((button)=>button.addEventListener("click",async()=>{
    const sessionId=button.dataset.openPackageSession;if(!sessionId||button.disabled)return;
    button.disabled=true;const oldText=button.textContent;button.textContent="Açılıyor…";
    try{await sendRuntime(MESSAGE.OPEN_FINAL_PACKAGE_FOLDER,{sessionId});}
    catch(error){console.error(error);setStatus("Final paket açılamadı",userFacingError(error),"danger","HATA");}
    finally{button.disabled=false;button.textContent=oldText;}
  }));
}
async function loadResumableJobs(){
  try{resumableSessions=await sendRuntime(MESSAGE.GET_RESUMABLE_SESSIONS,{limit:8,excludeSessionId:activeSession?.sessionId||null})||[];}catch{resumableSessions=[];}
  renderResumableJobs();
}
async function resumeSavedJob(sessionId){
  if(busy||!sessionId)return;busy=true;render(activeSession);
  setHealth("İşe dönülüyor","warn");
  setStatus("Yarım kalan işe dönülüyor","UYAP dosyası otomatik açılıyor, güncel evrak listesi doğrulanıyor ve kaldığı yerden devam hazırlanıyor.","info","HIZLI DEVAM");
  let actionError=null;
  try{
    let tab=null;try{tab=await activeUyapTab();}catch{}
    activeSession=await sendRuntime(MESSAGE.RESUME_SAVED_SESSION,{sessionId,tabId:tab?.id||null,autoStart:true});
    await loadResumableJobs();
  }catch(error){console.error(error);actionError=userFacingError(error);}
  finally{busy=false;render(activeSession);renderResumableJobs();if(actionError)setStatus("Yarım işe dönülemedi",actionError,"danger","HATA");}
}

function render(session){
  activeSession=session||null;
  renderResumableJobs();
  if(session?.sessionId && casePickerMode===null) el.casePickerPanel.hidden=true;
  const c=counts(session),total=Number(session?.total||0),downloaded=downloadedCount(session),localExisting=localExistingCount(session),completed=completedCount(session),review=reviewCount(session),deferred=deferredCount(session);
  const pct=total?Math.min(100,Math.round(completed/total*100)):0;
  const perf=session?.performance||{};
  const rate=Number(perf.sessionAvgDocsPerMinute||perf.docsPerMinute||0);
  const last=session?.updatedAt||perf.updatedAt||session?.lastHeartbeatAt||null;

  el.jobContext.textContent=contextLabel(session); el.jobContext.title=contextLabel(session);
  el.sourceMode.textContent=session?`${sourceLabel(session)} • ${session.status||"-"}`:"Kaynak bekleniyor";
  el.verified.textContent=downloaded.toLocaleString("tr-TR"); el.localExisting.textContent=localExisting.toLocaleString("tr-TR"); el.completedCount.textContent=completed.toLocaleString("tr-TR"); el.total.textContent=total.toLocaleString("tr-TR"); el.remainingCount.textContent=Math.max(0,total-completed).toLocaleString("tr-TR");
  el.progressPercent.textContent=`${pct}%`; el.progressBar.style.width=`${pct}%`;
  el.etaLine.textContent=formatEta(Math.max(0,total-completed-review),rate); el.speedLine.textContent=rate>0?`${formatRate(rate)} indirme/dk`:"İndirme hızı ölçülmedi";
  el.deferred.textContent=String(deferred); el.review.textContent=String(review); el.warnings.textContent=String(Number(session?.warningCount||0));
  el.lastActivity.textContent=relativeTime(last);
  el.loginRoutePanel.hidden=!(session?.sourceSystem==="UYAP_KURUM"&&session?.status==="paused"&&session?.pauseReason==="LOGIN_REQUIRED");
  el.safeCount.textContent=completed.toLocaleString("tr-TR");
  el.attentionBanner.hidden=review<=0; el.attentionText.textContent=`${review} kayıt sizden işlem bekliyor`;

  el.reportButton.disabled=!session?.sessionId;
  el.rebindButton.disabled=busy||["running","recovering"].includes(session?.status);
  el.newJobButton.disabled=busy;

  if(casePickerMode && !el.casePickerPanel.hidden){
    primaryAction="none"; el.primaryButton.textContent="Dosya seçimi açık"; el.primaryButton.disabled=true;
    setHealth("Dosya seçimi","good");
    setStatus("Arşivlenecek dosyayı seçin",`${el.casePickerSelect.options.length} UYAP Kurum dosyası bulundu. Seçiminizden sonra dosya otomatik açılacak ve gerçek evrak listesi taranacak.`,"info","DOSYA SEÇİMİ");
    return;
  }

  if(!session){
    primaryAction="start"; el.primaryButton.textContent="Arşivlemeyi başlat"; el.primaryButton.disabled=busy;
    setHealth(agentHealth?.ok?"Sistem hazır":agentHealth===null?"Kontrol ediliyor":agentHealth?.errorCode==="NATIVE_HOST_NOT_FOUND"?"Ajan kaydı bulunamadı":agentHealth?.errorCode==="NATIVE_HOST_FORBIDDEN"?"Ajan izni hatalı":agentHealth?.errorCode==="AGENT_VERSION_MISMATCH"?"Ajan sürümü uyumsuz":"Ajan bağlantısı gerekli",agentHealth?.ok?"good":agentHealth===null?"neutral":"bad");
    setStatus(agentHealth?.ok?"Arşivlemeye hazır":agentHealth===null?"Yerel ajan kontrol ediliyor":"Yerel ajan bağlantısı kurulamadı",agentHealth?.ok?"UYAP'ta ilgili dava veya evrak ekranını açın. İlk tarama ve güvenlik kontrolü otomatik yapılır.":agentHealth===null?"Kurulum durumu kontrol ediliyor…":(agentHealth?.error||"YEREL_AJANI_KUR.bat dosyasını yeniden çalıştırın."),agentHealth?.ok?"info":agentHealth===null?"info":"danger",agentHealth?.ok?"DURUM":agentHealth===null?"DURUM":"AJAN");
    return;
  }

  const status=session.status,pause=session.pauseReason;
  if(status==="running"){
    primaryAction="pause"; el.primaryButton.textContent="Duraklat"; el.primaryButton.disabled=busy;
    const localFound=Number(session?.localArchiveRecognizedCount||0);
    const large = session.largeCase?.enabled ? session.largeCase : null;
    const autoProgress = large ? ` • AUTO işlem ${large.currentBatch}/${large.totalBatches} (%${large.batchPercent})` : " • AUTO motor";
    const cooldownUntil=Date.parse(session?.contentCooldownUntil||"");
    const inContentCooldown=session?.sessionGuardStatus==="CONTENT_COOLDOWN"&&Number.isFinite(cooldownUntil)&&cooldownUntil>Date.now();
    if(inContentCooldown){
      const remaining=Math.max(1,Math.ceil((cooldownUntil-Date.now())/1000));
      setHealth("UYAP kısa bekleme","warn");
      setStatus("HTML yanıtları arttı, akış korunuyor",`${downloaded.toLocaleString("tr-TR")} evrak indirildi ve doğrulandı. Yalnız yeni UYAP isteği ${remaining} sn bekletiliyor; yerel doğrulama ve arşiv işleri sürüyor. Sistem oturumu recovery durumuna alınmadı.`,"warning","ADAPTİF KORUMA");
    }else{
      setHealth("Çalışıyor","good"); setStatus("Arşivleme sürüyor",`${downloaded.toLocaleString("tr-TR")} evrak indirildi ve doğrulandı${localExisting?` • ${localExisting.toLocaleString("tr-TR")} kayıt önceden yerelde`:""} • ${completed.toLocaleString("tr-TR")}/${total.toLocaleString("tr-TR")} sonuçlandı${autoProgress}. Hız, batch, recovery ve güvenlik kararları otomatik yönetiliyor.`,"success");
    }
  }else if(status==="recovering"){
    if(session?.sourceSystem==="UYAP_KURUM"&&completed===0){
      primaryAction="newJob"; el.primaryButton.textContent="Açık dosyadan yeniden tara"; el.primaryButton.disabled=busy;
      const reason=String(session?.pauseReason||"");
      const sourceMismatch=reason==="SOURCE_PAGE_NOT_READY";
      const startPath=reason==="START_PATH_DEGRADED";
      const startBindingFailed=reason==="START_BINDING_FAILED";
      const runtimeRecovery=reason==="QUEUE_RUNTIME_RECOVERY";
      setHealth(sourceMismatch?"Kaynak ekranı eşleşmiyor":startPath?"Başlangıç yolu toparlanıyor":runtimeRecovery?"Evrak bağı yenileniyor":"Dosya seçimi gerekli","warn");
      setStatus(
        sourceMismatch?"Açık UYAP Kurum dosyası doğrulanamadı":startPath?"Başlangıç indirme yolu yeniden bağlanıyor":runtimeRecovery?"Evrak bağları yenileniyor":"UYAP Kurum dosyasını yeniden seçin",
        sourceMismatch?"Açık dosyanın kaynak kimliği aktif arşiv işiyle doğrulanamadı.":startPath?"Kaynak dosya ve evrak envanteri doğru; ilk gerçek indirme yoluna geçmeden önce runtime bağları güvenli biçimde yenileniyor.":runtimeRecovery?"Açık dava doğru ancak bazı runtime evrak bağları yeniden eşleştiriliyor.":"Dosya listesine dönüp ‘Açık dosyadan yeniden tara’ düğmesine basın. Dosya seçimi ve ‘Dosya Görüntüle’ adımı UYAP Archive tarafından yönetilecek.",
        "warning","EYLEM GEREKLİ"
      );
    }else{
      primaryAction="none"; el.primaryButton.disabled=true;
      if(pause==="CONTENT_ENDPOINT_CIRCUIT_OPEN"){
        const until=Date.parse(session?.contentCooldownUntil||"");
        const remaining=Number.isFinite(until)?Math.max(0,Math.ceil((until-Date.now())/1000)):null;
        el.primaryButton.textContent=remaining!==null&&remaining>0?`UYAP ${remaining} sn dinlendiriliyor…`:"Kontrollü test hazırlanıyor…";
        setHealth("UYAP yanıtı bekleniyor","warn");
        setStatus("Belge servisi kısa süre dinlendiriliyor",`Art arda HTML yanıtı algılandı. Yeni UYAP indirmeleri geçici olarak durdu; mevcut yerel ve doğrulama işlemleri korunuyor${remaining!==null&&remaining>0?`. ${remaining} sn sonra tek kontrollü deneme yapılacak.`:". Bir sonraki istek kontrollü olarak test edilecek."}`,"warning","OTOMATİK KORUMA");
      }else{
        el.primaryButton.textContent="Otomatik toparlanıyor…";
        setHealth("Toparlanıyor","warn"); setStatus("Geçici sorun algılandı","İşiniz ve doğrulanmış dosyalarınız korunuyor. Sistem güvenli biçimde yeniden bağlanmayı deniyor.","warning");
      }
    }
  }else if(status==="paused"&&pause==="SOURCE_CONTEXT_CHANGED"){
    primaryAction="newJob"; el.primaryButton.textContent="Açık dosyaya geç"; el.primaryButton.disabled=busy;
    const observed=session?.observedCaseId||"başka dosya";
    setHealth("Dosya değişti","warn");
    setStatus("Aktif iş güvenle duraklatıldı",`Arşiv işi ${session.caseId||""} dosyasına ait, açık UYAP dosyası ${observed}. Bekleme/recovery döngüsü durduruldu. Açık dosyaya geçerek yeni işi seri biçimde başlatabilirsiniz.`,"warning","DOSYA DEĞİŞTİ");
  }else if(status==="paused"&&pause==="ALREADY_ARCHIVED"){
    primaryAction="none"; el.primaryButton.textContent="Yeniden indirme yapılmadı"; el.primaryButton.disabled=true;
    setHealth("Zaten arşivde","good");
    setStatus("Bu dosya daha önce arşivlenmiş.","Evraklar yerel arşivde mevcut olduğu için yeniden indirme yapılmadı.","success","ARŞİVDE");
  }else if(status==="paused"&&pause==="START_BINDING_FAILED"){
    const localFound=Number(session?.localArchiveRecognizedCount||0);
    const allAlreadyLocal=total>0&&completed===total&&localFound>=total;
    if(allAlreadyLocal){
      primaryAction="none"; el.primaryButton.textContent="Yeniden indirme yapılmadı"; el.primaryButton.disabled=true;
      setHealth("Zaten arşivde","good");
      setStatus("Bu dosya daha önce arşivlenmiş.","Evraklar yerel arşivde mevcut olduğu için yeniden indirme yapılmadı.","success","ARŞİVDE");
    }else{
      primaryAction="scan"; el.primaryButton.textContent="Açık dosyadan yeniden tara"; el.primaryButton.disabled=busy;
      setHealth("Dosya doğrulanamadı","warn");
      setStatus("Arşivleme başlatılamadı","Açık UYAP dosyası ile arşivlenecek evraklar doğrulanamadı. Doğru dosyanın açık olduğundan emin olup yeniden tarayın.","warning","KONTROL GEREKLİ");
    }
  }else if(status==="paused"&&pause==="LOGIN_REQUIRED"){
    primaryAction=session.sourceSystem==="UYAP_KURUM"?"login":"start"; el.primaryButton.textContent=session.sourceSystem==="UYAP_KURUM"?"Yeniden giriş yap":"Arşivlemeye devam et"; el.primaryButton.disabled=busy;
    setHealth("Giriş gerekli","bad"); setStatus("UYAP oturumu sona erdi",`${completed.toLocaleString("tr-TR")} sonuçlanmış evrak güvende. Yeniden giriş yaptıktan sonra aynı işten devam edilir.`,"danger","EYLEM GEREKLİ");
  }else if(status==="paused"&&pause==="CONTENT_ENDPOINT_REFRESH_REQUIRED"){
    primaryAction="start"; el.primaryButton.textContent="Yeniledim, güvenli devam et"; el.primaryButton.disabled=busy;
    setHealth("UYAP yenilemesi gerekli","bad");
    setStatus("Belge servisi hata yanıtı veriyor",`${completed.toLocaleString("tr-TR")} sonuçlanmış evrak ve tüm kuyruk güvende. UYAP dava/Evrak sekmesini bir kez yenileyin; evrak listesi tekrar göründüğünde bu düğmeyle devam edin. Sistem yeni istek göndermeyi durdurdu.`,"danger","GÜVENLİ DURAKLATMA");
  }else if(status==="incomplete"){
    const allVerified=total>0&&completed===total&&review===0&&deferred===0;
    primaryAction=allVerified?"repackage":"problems"; el.primaryButton.textContent=allVerified?"Final paketi oluştur":"Sorunları incele"; el.primaryButton.disabled=busy;
    setHealth(allVerified?"Paketleme gerekli":"Müdahale gerekli",allVerified?"warn":"bad");
    const finalError=String(session?.finalizationError||"").replace(/^AUTO_FINAL_PACKAGE_FAILED:\s*/i,"").replace(/^(?:Error:\s*)+/i,"").trim();
    const finalReason=finalError&&!["SOURCE_RESCAN_UNAVAILABLE_LOCAL_FINALIZATION","LOCAL_REPACKAGE_FAILED"].includes(finalError)?` Paketleme nedeni: ${finalError.slice(0,220)}`:"";
    setStatus(allVerified?"Evraklar güvende, final paket eksik":"Arşiv tamamlanamadı",allVerified?`Tüm evraklar doğrulandı. Otomatik final paketleme tamamlanamadı; ZIP ve manifesti yeniden indirmeden şimdi oluşturabilirsiniz.${finalReason}`:`${review+deferred} kayıt tamamlanmayı bekliyor. Doğrulanmış dosyalar korunuyor.`,allVerified?"warning":"danger",allVerified?"SON ADIM":"EYLEM GEREKLİ");
  }else if(status==="completed"&&session.archiveStatus==="VERIFIED_COMPLETE"){
    const parts=Number(session.finalIntegrity?.partCount||0);
    primaryAction="newJob"; el.primaryButton.textContent="Yeni arşiv işi"; el.primaryButton.disabled=busy;
    setHealth("Tamamlandı","good");
    const excluded=userExcludedCount(session),packaged=Math.max(0,completed-excluded),exclusionOnly=session.finalIntegrity?.allDocumentsUserExcluded===true;setStatus(exclusionOnly?"Kontrol tamamlandı":"Arşiv başarıyla tamamlandı",exclusionOnly?`${excluded.toLocaleString("tr-TR")} kayıt kullanıcı onayıyla hariç tutuldu. Bu iş kayıtlı işler listesine alındı; yeni bir arşiv işi başlatabilirsiniz.`:`${packaged.toLocaleString("tr-TR")} evrak paketlendi${excluded?` • ${excluded.toLocaleString("tr-TR")} HTML kullanıcı onayıyla hariç tutuldu`:""}${parts?` • ${parts} ZIP parçası`:""} • bütünlük kapısı geçti. İş kayıtlılar listesinde; yeni dosya için hazırsınız.`,"success","TAMAMLANDI");
  }else{
    const resumed = session?.resumedExisting === true && Number(session?.resumeFromVerified||0) > 0;
    primaryAction="start";
    el.primaryButton.textContent=status==="paused"?"Arşivlemeye devam et":resumed?"Kaldığı yerden devam et":"Arşivlemeyi başlat";
    el.primaryButton.disabled=busy;
    setHealth(status==="paused"?"Duraklatıldı":resumed?"Devam etmeye hazır":"Hazır",status==="paused"?"warn":"good");
    const pauseText=pause==="START_PREFLIGHT_FAILED"?"Başlangıç güvenlik kontrolü geçemedi. UYAP ekranını ve yerel ajanı kontrol edin.":pause==="SOURCE_REBIND_REQUIRED"?"UYAP ekranı yeniden bağlanmalı. Doğrulanmış evraklar korunuyor.":pause==="SOURCE_INVENTORY_CHANGED"?(session?.queueError||"UYAP evrak listesi önceki yarım işle karşılaştırıldığında değişmiş görünüyor. Doğrulanmış evraklar korunuyor."):pause==="SOURCE_CONTEXT_CHANGED"?"Açık UYAP dosyası değişti. Eski iş güvenli biçimde duraklatıldı; yeni dosya için yeni işe geçebilirsiniz.":"İş ve doğrulanmış dosyalar korunuyor. Hazır olduğunuzda devam edebilirsiniz.";
    const localState=String(session?.localArchiveScanState||"");
    const localFound=Number(session?.localArchiveRecognizedCount||0);
    const localAmbiguous=Number(session?.localArchiveAmbiguousCount||0);
    const localMissing=Number(session?.localArchiveUnmatchedCount||0);
    const localSummary=localState==="complete"
      ? ` Yerel arşiv kontrolü: ${localFound.toLocaleString("tr-TR")} bulundu${localAmbiguous?` • ${localAmbiguous.toLocaleString("tr-TR")} belirsiz`:""}${localMissing?` • ${localMissing.toLocaleString("tr-TR")} bulunamadı`:""}.`
      : localState==="partial"?` Yerel arşiv indeksi AUTO olarak parça parça hazırlanıyor${localFound?` • ${localFound.toLocaleString("tr-TR")} mevcut kayıt tanındı`:""}.`
      : localState==="unavailable"?" Yerel arşiv kontrolü yapılamadı; arşivleme başlamadan önce tekrar denenecek.":"";
    const readyText=resumed
      ? `${Number(session?.resumeFromVerified||downloaded).toLocaleString("tr-TR")}/${total.toLocaleString("tr-TR")} doğrulanmış evrak korunuyor. Bu dosyaya ait yarım iş bulundu; kalan evraklardan devam edilecek.${localSummary}`
      : `${total.toLocaleString("tr-TR")} benzersiz evrak bulundu.${localSummary}`;
    setStatus(status==="paused"?"Arşivleme duraklatıldı":resumed?"Yarım kalan iş bulundu":localState==="complete"?"Yerel arşiv kontrol edildi":"İş başlamaya hazır",status==="paused"?pauseText:readyText,status==="paused"?"warning":"info");
  }
}

async function refresh(){
  if(busy) return;
  try{
    [activeSession,operation]=await Promise.all([sendRuntime(MESSAGE.GET_ACTIVE_SESSION),sendRuntime(MESSAGE.GET_OPERATION)]);
    if(Date.now()-lastRecentArchiveCheck>60000){
      lastRecentArchiveCheck=Date.now();
      sendRuntime(MESSAGE.GET_ARCHIVE_REGISTRY,{limit:8,browseCache:true}).then(value=>{recentArchiveRegistry=value;renderRecentArchive();}).catch(()=>{});
    }
    if(Date.now()-lastAgentCheck>15000){lastAgentCheck=Date.now();sendRuntime("ping_agent").then(v=>{agentHealth=v;render(activeSession);}).catch(()=>{agentHealth={ok:false};render(activeSession);});}
    render(activeSession);
    renderRecentArchive();
    await loadResumableJobs();
    if(["scanning","starting","resuming"].includes(operation?.phase)){
      el.primaryButton.disabled=true; el.primaryButton.textContent=operation.phase==="scanning"?"Evraklar taranıyor…":operation.phase==="resuming"?"Yarım işe dönülüyor…":"Güvenlik kontrolü yapılıyor…";
    }
  }catch(error){console.error(error);setHealth("Kontrol gerekli","bad");setStatus("Sistem bilgisi alınamadı",userFacingError(error),"danger","HATA");}
}

async function performPrimary(){
  if(busy) return; busy=true; render(activeSession);
  let actionError=null;
  try{
    if(primaryAction==="pause"){
      activeSession=await sendRuntime(MESSAGE.PAUSE_SESSION,{sessionId:activeSession.sessionId});
    }else if(primaryAction==="report"){
      await openReport();
    }else if(primaryAction==="package"){
      await sendRuntime(MESSAGE.OPEN_FINAL_PACKAGE_FOLDER,{sessionId:activeSession.sessionId});
    }else if(primaryAction==="problems"){
      await openWorkspace("problems");
    }else if(primaryAction==="repackage"){
      activeSession=await sendRuntime(MESSAGE.REPACKAGE_ARCHIVE,{sessionId:activeSession.sessionId});
    }else if(primaryAction==="login"){
      await sendRuntime(MESSAGE.OPEN_LOGIN_ROUTE,{sessionId:activeSession.sessionId,route:"direct"});
    }else if(primaryAction==="newJob"){
      const tab=await activeUyapTab();
      if(!await offerKurumCaseSelection("newJob",tab)) activeSession=await sendRuntime(MESSAGE.NEW_JOB,{tabId:tab.id});
    }else if(primaryAction==="start"){
      const tab=await activeUyapTab();
      if(!activeSession?.sessionId && await offerKurumCaseSelection("start",tab)) return;
      activeSession=await sendRuntime(MESSAGE.START_SESSION,{sessionId:activeSession?.sessionId||null,tabId:tab.id});
    }
  }catch(error){console.error(error);actionError=userFacingError(error);}
  finally{busy=false;render(activeSession);if(actionError)setStatus("İşlem tamamlanamadı",actionError,"danger","HATA");}
}

el.primaryButton.addEventListener("click",()=>void performPrimary());
el.workspaceButton.addEventListener("click",()=>void openWorkspace("overview"));
el.recentArchiveOpen.addEventListener("click",()=>void openWorkspace("archive"));
el.selectExistingArchiveButton.addEventListener("click",async()=>{
  if(busy)return;busy=true;el.selectExistingArchiveButton.disabled=true;
  setStatus("Klasör seçme penceresi açıldı","Öne gelen pencereden UYAP_Arsiv klasörünü veya onu içeren üst klasörü seçin.","info","ARŞİVDEN DEVAM");
  try{
    const result=await sendRuntime(MESSAGE.SELECT_EXISTING_ARCHIVE);
    if(result?.cancelled){setStatus("Seçim iptal edildi","Mevcut arşiv ayarı değiştirilmedi.","info","ARŞİVDEN DEVAM");return;}
    recentArchiveRegistry=result;lastRecentArchiveCheck=Date.now();renderRecentArchive();
    setStatus("Eski arşiv bağlandı",`${Number(result.physicalReadyFiles||result.presentFiles||0).toLocaleString("tr-TR")} fiziksel dosya bulundu. Şimdi aynı UYAP dosyasını açıp arşivlemeyi başlatın; yalnız eksikler indirilecek.`,"success","ARŞİVDEN DEVAM");
  }catch(error){console.error(error);setStatus("Arşiv bağlanamadı",userFacingError(error),"danger","HATA");}
  finally{busy=false;el.selectExistingArchiveButton.disabled=false;}
});
el.reviewCard.addEventListener("click",()=>void openWorkspace("problems"));
el.deferredCard.addEventListener("click",()=>void openWorkspace("problems"));
el.attentionBanner.addEventListener("click",()=>void openWorkspace("problems"));
el.reportButton.addEventListener("click",()=>void openReport());
el.supportButton.addEventListener("click",()=>void openWorkspace("support"));
el.rebindButton.addEventListener("click",async()=>{
  if(busy)return;busy=true;render(activeSession);let actionError=null;try{const tab=await activeUyapTab();activeSession=await sendRuntime("scan_session",{tabId:tab.id});}catch(e){console.error(e);actionError=userFacingError(e);}finally{busy=false;render(activeSession);if(actionError)setStatus("UYAP ekranı bağlanamadı",actionError,"danger","HATA");}
});
el.newJobButton.addEventListener("click",async()=>{
  if(busy)return;const ok=globalThis.confirm?confirm("Açık UYAP dosyasına geçilsin mi? Mevcut iş ve indirilen dosyalar korunur. Bu dosyaya ait yarım bir arşiv işi varsa yeni baştan oluşturmak yerine kaldığı yerden devam edilir."):true;if(!ok)return;
  busy=true;render(activeSession);let actionError=null;try{const tab=await activeUyapTab();if(!await offerKurumCaseSelection("newJob",tab))activeSession=await sendRuntime(MESSAGE.NEW_JOB,{tabId:tab.id});}catch(e){console.error(e);actionError=userFacingError(e);}finally{busy=false;render(activeSession);if(actionError)setStatus("Yeni iş oluşturulamadı",actionError,"danger","HATA");}
});
el.casePickerCancel.addEventListener("click",()=>{hideCasePicker();render(activeSession);});
el.casePickerOpen.addEventListener("click",async()=>{
  if(busy||!casePickerTabId||!casePickerMode)return;
  const caseId=el.casePickerSelect.value;if(!caseId)return;
  busy=true;el.casePickerOpen.disabled=true;setStatus("Dosya açılıyor",`${caseId} için UYAP Kurum ‘Dosya Görüntüle’ adımı çalıştırılıyor. Gerçek evrak ekranı doğrulanınca arşivleme otomatik başlayacak.`,"info","UYAP KURUM");
  let actionError=null;try{activeSession=await sendRuntime(MESSAGE.OPEN_KURUM_CASE,{tabId:casePickerTabId,caseId,newJob:casePickerMode==="newJob"});hideCasePicker();}catch(e){console.error(e);actionError=userFacingError(e);}finally{busy=false;el.casePickerOpen.disabled=false;render(activeSession);if(actionError)setStatus("Dosya açılamadı",actionError,"danger","HATA");}
});
el.kurumDirectLoginButton.addEventListener("click",()=>void sendRuntime(MESSAGE.OPEN_LOGIN_ROUTE,{sessionId:activeSession?.sessionId,route:"direct"}));
el.kurumEdevletLoginButton.addEventListener("click",()=>void sendRuntime(MESSAGE.OPEN_LOGIN_ROUTE,{sessionId:activeSession?.sessionId,route:"edevlet"}));

void refresh();
setInterval(()=>void refresh(),4000);
