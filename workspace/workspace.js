const MESSAGE=Object.freeze({
  GET_ACTIVE_SESSION:"get_active_session",GET_SESSIONS:"get_sessions",GET_REPORT_DATA:"get_report_data",GET_AI_CONTEXT:"get_ai_context",GET_AI_SERVICE_STATUS:"get_ai_service_status",BUILD_AI_INDEX:"build_ai_index",GET_AI_INDEX_STATUS:"get_ai_index_status",SEARCH_AI_INDEX:"search_ai_index",AI_ASK:"ai_ask",
  GET_AI_ARCHIVE_CASES:"get_ai_archive_cases",BUILD_AI_ARCHIVE_CASE_INDEX:"build_ai_archive_case_index",GET_AI_ARCHIVE_CASE_INDEX_STATUS:"get_ai_archive_case_index_status",SEARCH_AI_ARCHIVE_CASE:"search_ai_archive_case",AI_ASK_ARCHIVE_CASE:"ai_ask_archive_case",GET_AI_ARCHIVE_SUMMARIES:"get_ai_archive_summaries",GET_AI_ARCHIVE_SUMMARY:"get_ai_archive_summary",DELETE_AI_ARCHIVE_SUMMARY:"delete_ai_archive_summary",RESTORE_AI_ARCHIVE_SUMMARY:"restore_ai_archive_summary",
  GET_DIAGNOSTIC_DATA:"get_diagnostic_data",GET_PROBLEM_ITEMS:"get_problem_items",RETRY_SELECTED_PROBLEMS:"retry_selected_problems",
  RESET_RUNTIME:"reset_runtime",REPACKAGE_ARCHIVE:"repackage_archive",NEW_JOB:"new_job",SOURCE_DIAGNOSTIC:"source_diagnostic",
  TEST_NOTIFICATION:"test_notification",OPEN_ARCHIVE_FOLDER:"open_archive_folder",GET_ARCHIVE_REGISTRY:"get_archive_registry",CONFIRM_HTML_EXCLUSION:"confirm_html_exclusion",OPEN_FINAL_PACKAGE_FOLDER:"open_final_package_folder"
});
const MANIFEST=chrome.runtime?.getManifest?.()||{};const VERSION=MANIFEST.version_name||MANIFEST.version||"2.9.0 BETA1 P1P2 TEST ONLY";
const SETTINGS_KEY="uyapUiSettingsV1";
const ONBOARDING_KEY="uyapUiOnboardingCompletedV1";
const AI_PRINT_SUMMARY_KEY="uyapAiPrintSummaryV1";
const DEFAULT_SETTINGS={loginNotification:true,completionNotification:true,problemNotification:true,showTechnical:true};
const PAGE_SIZE=100;

const $=id=>document.getElementById(id);
const state={sessions:[],selectedId:null,selectedArchiveKey:null,summary:null,report:null,archiveRegistry:null,problems:null,agent:null,view:"overview",archivePage:0,archivePageData:[],archivePageTotal:0,archivePageLoading:false,archivePageError:null,archivePageRequestId:0,archiveRegistryError:null,problemTab:"review",selectedProblemKeys:new Set(),settings:{...DEFAULT_SETTINGS},onboardingStep:0,aiPrompt:"",aiWorkflowId:"ask",aiContext:null,aiService:null,aiIndex:null,aiCases:[],aiSelectedCaseKey:null,aiSummaries:[],aiCurrentResult:null,timelineDocs:[],account:null,device:null};
let toastTimer=null;
let archiveRegistryRefreshTask=null;
let workspaceLiveRefreshTask=null;
let lastWorkspaceSessionsRefreshAt=0;
let lastWorkspaceReportRefreshAt=0;
let lastWorkspaceRegistryRefreshAt=0;
let workspaceReportDirty=true;
let workspaceRegistryDirty=true;
const WORKSPACE_LIVE_REFRESH_MS=4_000;
const WORKSPACE_SESSIONS_REFRESH_MS=15_000;
const WORKSPACE_REPORT_REFRESH_MS=12_000;
const WORKSPACE_REGISTRY_REFRESH_MS=15_000;
// Ignore stale AI responses when the user changes cases or navigates away.
let aiWorkspaceRequestId=0;
let aiHistoryRequestId=0;
const AI_HISTORY_DISPLAY_TIMEOUT_MS=15_000;

async function sendRuntime(type,payload={}){
  const response=await chrome.runtime.sendMessage({type,...payload});
  if(!response?.ok) throw new Error(response?.error||"İşlem başarısız.");
  return response.data;
}
function esc(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function n(value){return Number(value||0);}
function counts(){return state.summary?.counts||{};}
function downloaded(){return n(counts().verified);}
function userExcluded(){return n(state.summary?.userExcludedCount||state.summary?.finalIntegrity?.userExcludedCount);}
function localExisting(){const recognized=n(state.summary?.localArchiveRecognizedCount);const localSkipped=Math.max(0,n(counts().skipped)-userExcluded());return Math.max(recognized,localSkipped);}
function verified(){return n(counts().verified)+n(counts().skipped);}
function problems(){return n(counts().review_required)+n(counts().failed);}
function deferred(){return n(counts().retry_wait);}
function sourceLabel(s){return s?.sourceSystem==="UYAP_KURUM"?"UYAP Kurum":"UYAP";}
function jobName(s){return [s?.institutionName||s?.courtName,s?.caseId].filter(Boolean).join(" • ")||"Arşiv işi";}
function formatDate(value){const d=new Date(value||0);return Number.isNaN(d.getTime())?"-":new Intl.DateTimeFormat("tr-TR",{dateStyle:"short",timeStyle:"short"}).format(d);}
function formatBytes(value){const bytes=Number(value||0);if(!Number.isFinite(bytes)||bytes<=0)return"0 B";const units=["B","KB","MB","GB","TB"];let n=bytes,i=0;while(n>=1024&&i<units.length-1){n/=1024;i+=1;}return`${n>=100||i===0?n.toFixed(0):n>=10?n.toFixed(1):n.toFixed(2)} ${units[i]}`;}
function statusInfo(s){
  const status=s?.status||"ready",pause=s?.pauseReason;
  if(status==="running")return{label:"Çalışıyor",tone:"good"};
  if(status==="recovering")return{label:"Toparlanıyor",tone:"warn"};
  if(status==="completed"&&s?.archiveStatus==="VERIFIED_COMPLETE")return{label:"Tamamlandı",tone:"good"};
  if(status==="incomplete")return{label:"Eksik",tone:"bad"};
  if(status==="paused"&&pause==="LOGIN_REQUIRED")return{label:"Giriş gerekli",tone:"bad"};
  if(status==="paused")return{label:"Duraklatıldı",tone:"warn"};
  return{label:"Hazır",tone:""};
}
function verifiedFor(s){const c=s?.counts||{};return n(c.verified)+n(c.skipped);}
function sessionHasFinalPackage(s){return Boolean(s&&(s.finalPackagePath||s.zipPath||(Array.isArray(s.archiveParts)&&s.archiveParts.some(part=>part?.path)))&&n(s.finalIntegrity?.partCount||s.archiveParts?.length)>0);}
function sameCaseKey(s){return [s?.sourceSystem||"UYAP",s?.institutionName||s?.courtName||"",s?.caseId||""].join("|").toLocaleLowerCase("tr-TR");}
function groupedSessions(){
  const mode=$("jobGrouping")?.value||"case";
  if(mode==="session")return state.sessions.map(s=>({session:s,count:1}));
  const map=new Map();
  for(const s of state.sessions){const key=sameCaseKey(s)||s.sessionId;const prev=map.get(key);if(!prev){map.set(key,{session:s,count:1});continue;}prev.count+=1;const a=new Date(prev.session.updatedAt||prev.session.createdAt||0).getTime(),b=new Date(s.updatedAt||s.createdAt||0).getTime();if(b>a)prev.session=s;}
  return [...map.values()].sort((a,b)=>new Date(b.session.updatedAt||b.session.createdAt||0)-new Date(a.session.updatedAt||a.session.createdAt||0));
}
function overviewNextAction(s){
  if(!s)return{title:"Yeni arşiv işi başlatın",text:"UYAP veya UYAP Kurum ekranını açın; Workspace açık sekmeyi kullanarak işi oluşturur.",label:"Yeni arşiv işi",action:"new"};
  const p=problems(),d=deferred(),total=n(s.total),done=verifiedFor(s),allVerified=total>0&&done===total&&p===0&&d===0;
  if(p>0)return{title:"Müdahale gereken kayıtlar var",text:`${p.toLocaleString("tr-TR")} kayıt otomatik tamamlanamadı. Doğrulanmış dosyalar korunuyor.`,label:"Sorunları incele",action:"problems"};
  if(d>0)return{title:"Otomatik tekrar kuyruğu bekliyor",text:`${d.toLocaleString("tr-TR")} kayıt yeniden denenecek. İsterseniz kuyruğu şimdi inceleyebilirsiniz.`,label:"Tekrar kuyruğunu aç",action:"problems"};
  if(s.status==="completed"&&sessionHasFinalPackage(s))return{title:"Arşiv hazır",text:"Final ZIP, manifest ve bütünlük çıktıları hazır. Paketi doğrudan açabilirsiniz.",label:"Final paketi aç",action:"package"};
  if(s.status==="incomplete"&&allVerified)return{title:"Yalnız final paket kaldı",text:"Evrakları yeniden indirmeden ZIP ve manifest oluşturabilirsiniz.",label:"Final paketi oluştur",action:"repackage"};
  if(s.status==="running"||s.status==="recovering")return{title:"Arşivleme sürüyor",text:`${done.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} kayıt sonuçlandı. Yerel arşivi canlı izleyebilirsiniz.`,label:"Yerel arşivi izle",action:"archive"};
  return{title:"İşi kontrol edin",text:"İş bekliyor veya duraklatılmış durumda. Ayrıntıları ve yerel kayıtları kontrol edin.",label:"İş ayrıntılarını aç",action:"archive"};
}
function renderWorkflow(s){
  const box=$("workflowFlow"),caption=$("flowCaption"),attention=$("attentionCard");
  if(!box||!caption||!attention)return;
  const total=n(s?.total),v=verifiedFor(s),p=s?problems():0,d=s?deferred():0,localState=String(s?.localArchiveScanState||"");
  const packageDone=Boolean(s?.status==="completed"&&s?.finalIntegrity?.gatePassed);
  const archiveDone=total>0&&v>=total-p-d;
  const steps=[
    {label:"1. Kaynak taraması",detail:total?`${total.toLocaleString("tr-TR")} evrak bulundu`:"UYAP envanteri bekleniyor",state:total?"done":"current"},
    {label:"2. Yerel arşiv kontrolü",detail:localState==="complete"?`${n(s?.localArchiveRecognizedCount).toLocaleString("tr-TR")} mevcut • ${n(s?.localArchiveUnmatchedCount).toLocaleString("tr-TR")} indirilecek`:localState==="failed"?"Kontrol tamamlanamadı":"Yerel kopyalar taranıyor",state:localState==="complete"?"done":total?"current":"pending"},
    {label:"3. Evrak arşivleme",detail:total?`${v.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} sonuçlandı`:"Bekliyor",state:archiveDone?"done":total?"current":"pending"},
    {label:"4. Sorun çözümü",detail:p?`${p.toLocaleString("tr-TR")} kayıt müdahale bekliyor`:d?`${d.toLocaleString("tr-TR")} otomatik tekrar bekliyor`:archiveDone?"Engel yok":"Bekliyor",state:p?"attention":d?"current":archiveDone?"done":"pending"},
    {label:"5. Final paket",detail:packageDone?`${n(s?.finalIntegrity?.verifiedPartCount).toLocaleString("tr-TR")} ZIP doğrulandı`:archiveDone&&p===0&&d===0?"Paketleme sırası":"Bekliyor",state:packageDone?"done":archiveDone&&p===0&&d===0?"current":"pending"}
  ];
  box.innerHTML=steps.map(step=>`<div class="flow-step ${step.state}"><span class="flow-dot">${step.state==="done"?"✓":step.state==="attention"?"!":""}</span><div><strong>${esc(step.label)}</strong><small>${esc(step.detail)}</small></div></div>`).join("");
  caption.textContent=packageDone?"Tamamlandı":p?"Müdahale gerekiyor":d?"Tekrarlar bekliyor":s?.status==="running"?"İşlem sürüyor":s?statusInfo(s).label:"İş bekleniyor";
  attention.hidden=true;attention.textContent="";
  if(!s)return;
  let title="",text="",action=null,actionLabel="";
  if(p){title="Kullanıcı işlemi gerekiyor";text=`${p.toLocaleString("tr-TR")} kayıt otomatik tamamlanamadı. Önce Sorun Merkezi'ni açın.`;action="problems";actionLabel="Sorunları incele";}
  else if(n(s.localArchiveAmbiguousCount)>0){title="Yerel arşiv eşleşmesi belirsiz";text=`${n(s.localArchiveAmbiguousCount).toLocaleString("tr-TR")} evrak için ikinci kopya indirmeyi önlemek amacıyla otomatik indirme engellenecek.`;action="archive";actionLabel="Yerel arşivi aç";}
  else if(packageDone){title="Final paket hazır";text="ZIP, manifest ve bütünlük çıktıları doğrulandı.";action="reports";actionLabel="Paket & raporu aç";}
  else if(n(s.localArchiveRecognizedCount)>0){title="Yerel kopyalar korundu";text=`${n(s.localArchiveRecognizedCount).toLocaleString("tr-TR")} evrak yerel arşivde bulundu ve yeniden indirme dışı bırakıldı.`;action="archive";actionLabel="Eşleşmeleri gör";}
  if(title){attention.hidden=false;attention.innerHTML=`<div><strong>${esc(title)}</strong><span>${esc(text)}</span></div>${action?`<button data-attention-action="${action}">${esc(actionLabel)}</button>`:""}`;attention.querySelector("[data-attention-action]")?.addEventListener("click",()=>showView(action));}
}
function shortPath(value,max=70){const v=String(value||"");return v.length>max?`…${v.slice(-(max-1))}`:v||"-";}
function showToast(message,error=false){const t=$("toast");t.textContent=message;t.className=`toast${error?" error":""}`;t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.hidden=true;},4500);}
function setBanner(message,tone="warn"){const b=$("globalBanner");if(!message){b.hidden=true;return;}b.hidden=false;b.textContent=message;b.className=`global-banner ${tone}`;}

async function getUyapTabs(){try{return await chrome.tabs.query({url:["https://*.uyap.gov.tr/*","https://uyap.gov.tr/*"]})||[];}catch{return[];}}
async function activeUyapTab(){const tabs=await getUyapTabs();const active=tabs.find(t=>t.active)||tabs[0];if(!active?.id)throw new Error("Açık bir UYAP/UYAP Kurum sekmesi bulunamadı.");return active;}
function openReport(){if(!state.selectedId)return;return chrome.tabs.create({url:chrome.runtime.getURL(`report/report.html?session=${encodeURIComponent(state.selectedId)}`)});}

async function loadSettings(){const data=await chrome.storage.local.get([SETTINGS_KEY,ONBOARDING_KEY]);state.settings={...DEFAULT_SETTINGS,...(data[SETTINGS_KEY]||{})};applySettingsForm();return data[ONBOARDING_KEY]===true;}
async function saveSettings(){await chrome.storage.local.set({[SETTINGS_KEY]:state.settings});applySettingsForm();showToast("Ayarlar kaydedildi.");}
function applySettingsForm(){
  $("settingLoginNotification").checked=state.settings.loginNotification!==false;
  $("settingCompletionNotification").checked=state.settings.completionNotification!==false;
  $("settingProblemNotification").checked=state.settings.problemNotification!==false;
  $("settingTechnical").checked=state.settings.showTechnical!==false;
  const tech=$("techDetails")?.closest(".panel");if(tech)tech.style.display=state.settings.showTechnical===false?"none":"block";
}

async function loadSessions({preserve=true}={}){
  // Oturumlar yalnız tarayıcı veritabanından gelir ve ağır yerel arşiv
  // taramasını beklememelidir. Böylece çalışan iş Workspace'e hemen yansır.
  const [sessions,active]=await Promise.all([sendRuntime(MESSAGE.GET_SESSIONS,{limit:200}),sendRuntime(MESSAGE.GET_ACTIVE_SESSION)]);
  state.sessions=Array.isArray(sessions)?sessions:[];
  const requested=new URLSearchParams(location.search).get("session");
  let id=preserve?state.selectedId:null;
  if(requested&&state.sessions.some(s=>s.sessionId===requested))id=requested;
  if(!id&&active?.sessionId)id=active.sessionId;
  if(!id&&state.sessions[0]?.sessionId)id=state.sessions[0].sessionId;
  state.selectedId=id||null;
  const archiveCases=archiveCaseEntries(),preservedArchive=preserve?state.selectedArchiveKey:null;
  state.selectedArchiveKey=state.selectedId?null:(archiveCases.some(item=>item.key===preservedArchive)?preservedArchive:(archiveCases[0]?.key||null));
  renderSessionPicker();
  await loadSelectedSummary();
  renderJobs();renderOverview();
  void refreshArchiveRegistryInBackground();
}
function renderArchiveData(){
  const docs=archiveRowsForScope(),typeSelect=$("archiveType"),currentType=typeSelect?.value||"";
  const localScope=$("archiveScope")?.value==="local";
  if(typeSelect){const types=[...new Set([...(localScope?[".pdf",".udf",".xml",".jpg",".jpeg",".png",".tif",".tiff",".doc",".docx",".zip",".html",".bin"]:[]),...docs.map(d=>(d.extension||d.contentKind||"").toString().toLowerCase()),currentType].filter(Boolean))].sort();typeSelect.innerHTML='<option value="">Tüm türler</option>'+types.map(t=>`<option value="${esc(t)}">${esc(t.replace(/^\./,"").toUpperCase())}</option>`).join("");typeSelect.value=types.includes(currentType)?currentType:"";}
  renderArchiveCaseIndexTable();renderArchiveRows();
}
function refreshArchiveRegistryInBackground({forceRefresh=false}={}){
  if(archiveRegistryRefreshTask)return archiveRegistryRefreshTask;
  const requestedSessionId=state.selectedId;
  const task=sendRuntime(MESSAGE.GET_ARCHIVE_REGISTRY,{sessionId:requestedSessionId,summaryOnly:true,limit:1,browseCache:true,forceRefresh}).then(registry=>{
    if(!registry)return null;
    if(requestedSessionId!==state.selectedId)return registry;
    state.archiveRegistry=registry;state.archiveRegistryError=null;
    if($("globalBanner")?.textContent.startsWith("Yerel arşiv"))setBanner("");
    if(forceRefresh)workspaceRegistryDirty=false;
    if(!state.selectedId){const archiveCases=archiveCaseEntries(),current=state.selectedArchiveKey;state.selectedArchiveKey=archiveCases.some(item=>item.key===current)?current:(archiveCases[0]?.key||null);}
    renderSessionPicker();renderJobs();renderRecentArchiveDocs();renderOverviewHealth();renderOverview();
    if(state.view==="archive"&&($("archiveScope")?.value||"local")==="local")renderArchiveData();
    return registry;
  }).catch(error=>{
    state.archiveRegistryError=String(error?.message||error);
    setBanner("Yerel arşiv özeti okunamadı: "+state.archiveRegistryError,"bad");
    if(state.view==="archive")renderArchiveRows();
    return null;
  }).finally(()=>{if(archiveRegistryRefreshTask===task)archiveRegistryRefreshTask=null;});
  archiveRegistryRefreshTask=task;
  return task;
}
function renderSessionPicker(){const select=$("sessionPicker"),archiveCases=archiveCaseEntries(),sessionCaseKeys=new Set(state.sessions.map(s=>archiveCaseKey({institutionName:s.institutionName||s.courtName,caseId:s.caseId})));select.textContent="";for(const s of state.sessions){const o=document.createElement("option");o.value=s.sessionId;o.textContent=`${sourceLabel(s)} • ${jobName(s)} • ${statusInfo(s).label}`;o.selected=s.sessionId===state.selectedId;select.append(o);}for(const item of archiveCases){if(sessionCaseKeys.has(item.key))continue;const o=document.createElement("option");o.value=`archive::${encodeURIComponent(item.key)}`;o.textContent=`Yerel arşiv • ${item.institutionName||"Bilinmeyen Kurum/Daire"} • ${normalizeArchiveCaseId(item.caseId)||"Dosya"} • ${n(item.physicalReadyCount??item.presentCount)}/${n(item.documentCount)}`;o.selected=!state.selectedId&&item.key===state.selectedArchiveKey;select.append(o);}if(!select.options.length){const o=document.createElement("option");o.value="";o.textContent="Henüz iş veya yerel arşiv yok";select.append(o);select.disabled=true;return;}select.disabled=false;}
async function loadSelectedSummary(){
  state.report=null;state.problems=null;state.selectedProblemKeys.clear();
  if(!state.selectedId){state.summary=null;renderAllSelectedViews();return;}
  try{state.summary=await sendRuntime(MESSAGE.GET_ACTIVE_SESSION,{sessionId:state.selectedId});}catch(e){state.summary=null;showToast(String(e).replace(/^Error:\s*/,""),true);}
  renderAllSelectedViews();
}
function renderAllSelectedViews(){renderOverview();renderProblemNavCount();if(state.view==="archive")void loadArchive();if(state.view==="timeline")void renderTimeline();if(state.view==="ai")void renderAiWorkspace();if(state.view==="problems")void loadProblems();if(state.view==="reports")void loadReports();if(state.view==="health")void runHealthCheck();if(state.view==="support")renderSupportDetails();}
function summaryRevision(s){
  if(!s)return "";
  const c=s.counts||{};
  return [s.updatedAt||"",s.status||"",s.pauseReason||"",s.archiveStatus||"",s.total||0,c.verified||0,c.skipped||0,c.queued||0,c.awaiting_browser||0,c.downloading||0,c.completed||0,c.retry_wait||0,c.review_required||0,c.failed||0,s.localArchiveRecognizedCount||0,s.localArchiveAmbiguousCount||0,s.localArchiveUnmatchedCount||0].join("|");
}
async function refreshWorkspaceLiveState(){
  if(document.visibilityState==="hidden"||workspaceLiveRefreshTask)return workspaceLiveRefreshTask;
  const task=(async()=>{
    const now=Date.now();
    const selectedId=state.selectedId;
    if(selectedId){
      try{
        const fresh=await sendRuntime(MESSAGE.GET_ACTIVE_SESSION,{sessionId:selectedId});
        if(selectedId===state.selectedId&&fresh){
          const changed=summaryRevision(fresh)!==summaryRevision(state.summary);
          state.summary=fresh;
          if(changed){
            state.problems=null;
            state.timelineDocs=[];
            workspaceReportDirty=true;
            workspaceRegistryDirty=true;
            renderOverview();renderProblemNavCount();renderJobs();renderRecentJobs();renderSessionPicker();
          }
          if(state.view==="archive"&&($("archiveScope")?.value||"session")==="session"&&(workspaceReportDirty||!state.report)&&now-lastWorkspaceReportRefreshAt>=WORKSPACE_REPORT_REFRESH_MS){
            lastWorkspaceReportRefreshAt=now;
            try{state.report=await sendRuntime(MESSAGE.GET_REPORT_DATA,{sessionId:selectedId});if(selectedId===state.selectedId){workspaceReportDirty=false;renderArchiveData();}}catch{}
          }
          if(state.view==="problems"&&changed)void loadProblems();
          if(state.view==="reports"&&changed)void loadReports();
        }
      }catch{}
    }
    if(now-lastWorkspaceSessionsRefreshAt>=WORKSPACE_SESSIONS_REFRESH_MS){
      lastWorkspaceSessionsRefreshAt=now;
      try{const sessions=await sendRuntime(MESSAGE.GET_SESSIONS,{limit:200});if(Array.isArray(sessions)){state.sessions=sessions;renderSessionPicker();renderJobs();renderRecentJobs();}}catch{}
    }
    const needsRegistry=(state.view==="archive"&&($("archiveScope")?.value||"local")==="local")||!state.selectedId||state.view==="overview";
    if(needsRegistry&&workspaceRegistryDirty&&now-lastWorkspaceRegistryRefreshAt>=WORKSPACE_REGISTRY_REFRESH_MS){
      lastWorkspaceRegistryRefreshAt=now;
      void refreshArchiveRegistryInBackground({forceRefresh:true});
    }
    return true;
  })().finally(()=>{if(workspaceLiveRefreshTask===task)workspaceLiveRefreshTask=null;});
  workspaceLiveRefreshTask=task;
  return task;
}

function renderOverview(){
  const s=state.summary,archiveCase=selectedArchiveCase(),hero=$("activeJobHero");
  const next=overviewNextAction(s);
  $("nextActionTitle").textContent=next.title;$("nextActionText").textContent=next.text;$("nextActionButton").textContent=next.label;$("nextActionButton").dataset.action=next.action;
  $("quickOpenArchive").disabled=!(s||archiveCase);$("quickOpenPackage").hidden=!sessionHasFinalPackage(s);
  if(!s){if(archiveCase){const total=n(archiveCase.documentCount),physical=n(archiveCase.physicalReadyCount??archiveCase.presentCount),problem=n(archiveCase.problemCount),pct=total?Math.min(100,Math.round(physical/total*100)):0;$("nextActionTitle").textContent="UYAP işini yerel arşivden yeniden bağlayın";$("nextActionText").textContent=`${physical.toLocaleString("tr-TR")} fiziksel evrak korunacak; açık UYAP ekranındaki güncel listeyle yalnız eksikler kuyruğa alınacak.`;$("nextActionButton").textContent="UYAP'tan işi yeniden oluştur";$("nextActionButton").dataset.action="new";hero.innerHTML=`<div class="hero-top"><div><p>YEREL ARŞİVDEN KURTARILDI</p><h2>${esc(archiveCase.institutionName||"Bilinmeyen Kurum/Daire")} • ${esc(normalizeArchiveCaseId(archiveCase.caseId)||archiveCase.caseTitle||"Dava dosyası")}</h2><p><strong>${physical.toLocaleString("tr-TR")}</strong> fiziksel evrak bulundu. Tarayıcı iş oturumu yok; dosya Workspace'e yerel arşiv kaydı olarak geri yansıtıldı.</p><div class="hero-facts"><span>${physical.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} fiziksel uygun</span><span>${problem.toLocaleString("tr-TR")} kontrol</span><span>Yeni indirme yapılmadı</span></div></div><span class="hero-state">Yerel arşiv</span></div><div class="hero-progress"><div class="hero-progress-track"><div style="width:${pct}%"></div></div><strong>${pct}%</strong></div>`;$("ovVerified").textContent=physical.toLocaleString("tr-TR");$("ovTotal").textContent=`${total.toLocaleString("tr-TR")} yerel kayıt`;$("ovDeferred").textContent="0";$("ovProblems").textContent=problem.toLocaleString("tr-TR");$("ovIntegrity").textContent="Yerel kayıt";$("ovParts").textContent="İş oturumu yeniden bağlanacak";$("snapLocal").textContent=`${physical.toLocaleString("tr-TR")} bulundu`;$("snapLocalDetail").textContent=`${Math.max(0,total-physical).toLocaleString("tr-TR")} fiziksel olarak hazır değil`;$("snapQueue").textContent="-";$("snapQueueDetail").textContent="UYAP taraması sonrası hesaplanacak";$("snapPackage").textContent="Bekliyor";$("snapPackageDetail").textContent="İş yeniden bağlandıktan sonra";}else{hero.innerHTML=`<div class="hero-top"><div><p>ÇALIŞMA MERKEZİ</p><h2>Arşiv işi bekleniyor</h2><p>UYAP veya UYAP Kurum ekranını açın. Yeni işi buradan veya popup üzerinden başlatabilirsiniz.</p></div><span class="hero-state">Hazır</span></div>`;["ovVerified","ovDeferred","ovProblems"].forEach(id=>$(id).textContent="0");$("ovTotal").textContent="0 toplam";$("ovIntegrity").textContent="-";$("ovParts").textContent="ZIP bekleniyor";$("snapLocal").textContent="-";$("snapLocalDetail").textContent="Henüz kontrol edilmedi";$("snapQueue").textContent="0";$("snapQueueDetail").textContent="Bekleyen kayıt yok";$("snapPackage").textContent="Bekliyor";$("snapPackageDetail").textContent="Bütünlük kontrolü bekleniyor";}renderWorkflow(null);renderRecentJobs();renderRecentArchiveDocs();renderOverviewHealth();renderAiDock(null);return;}
  const total=n(s.total),v=verified(),dl=downloaded(),local=localExisting(),pct=total?Math.round(v/total*100):0,info=statusInfo(s),matched=n(s.localArchiveRecognizedCount),ambiguous=n(s.localArchiveAmbiguousCount),missing=n(s.localArchiveUnmatchedCount),integrity=s.finalIntegrity||{};
  hero.innerHTML=`<div class="hero-top"><div><p>${esc(sourceLabel(s).toUpperCase())} • ${esc(formatDate(s.createdAt))}</p><h2>${esc(jobName(s))}</h2><p><strong>${dl.toLocaleString("tr-TR")}</strong> evrak yerele indirildi ve doğrulandı • ${local.toLocaleString("tr-TR")} kayıt önceden yerelde</p><div class="hero-facts"><span>${v.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} arşivde güvenli</span><span>${missing.toLocaleString("tr-TR")} indirilecek</span>${ambiguous?`<span>${ambiguous.toLocaleString("tr-TR")} belirsiz</span>`:""}<span>${sourceLabel(s)}</span></div></div><span class="hero-state">${esc(info.label)}</span></div><div class="hero-progress"><div class="hero-progress-track"><div style="width:${Math.min(100,pct)}%"></div></div><strong>${pct}%</strong></div>`;
  $("ovVerified").textContent=dl.toLocaleString("tr-TR");$("ovTotal").textContent=`${local.toLocaleString("tr-TR")} önceden yerelde • ${total.toLocaleString("tr-TR")} toplam`;$("ovDeferred").textContent=deferred().toLocaleString("tr-TR");$("ovProblems").textContent=problems().toLocaleString("tr-TR");$("ovIntegrity").textContent=integrity.gatePassed?"Geçti":s.status==="incomplete"?"Geçmedi":"Bekliyor";$("ovParts").textContent=n(integrity.partCount)?`${n(integrity.verifiedPartCount)}/${n(integrity.partCount)} ZIP doğrulandı`:"ZIP bekleniyor";
  $("snapLocal").textContent=`${matched.toLocaleString("tr-TR")} bulundu`;$("snapLocalDetail").textContent=ambiguous?`${ambiguous.toLocaleString("tr-TR")} belirsiz • ${missing.toLocaleString("tr-TR")} bulunamadı`:`${missing.toLocaleString("tr-TR")} yerelde yok`;
  const q=problems()+deferred();$("snapQueue").textContent=q.toLocaleString("tr-TR");$("snapQueueDetail").textContent=problems()?`${problems().toLocaleString("tr-TR")} müdahale • ${deferred().toLocaleString("tr-TR")} tekrar`:deferred()?`${deferred().toLocaleString("tr-TR")} otomatik tekrar`:"Bekleyen kayıt yok";
  $("snapPackage").textContent=integrity.gatePassed?"Hazır":s.status==="incomplete"&&v===total?"Oluşturulacak":"Bekliyor";$("snapPackageDetail").textContent=integrity.gatePassed?`${n(integrity.verifiedPartCount).toLocaleString("tr-TR")} ZIP parçası doğrulandı`:s.status==="incomplete"&&v===total?"Evraklar hazır, finalizasyon bekliyor":"Arşivleme tamamlanınca üretilecek";
  renderWorkflow(s);renderRecentJobs();renderRecentArchiveDocs();renderOverviewHealth();renderAiDock(s);
}
function renderRecentJobs(){const box=$("recentJobs");box.textContent="";const groups=groupedSessions().slice(0,5);if(!groups.length){box.innerHTML='<div class="empty-state">Henüz iş yok.</div>';return;}for(const group of groups){const s=group.session,row=document.createElement("div");row.className="compact-row";const info=statusInfo(s);row.innerHTML=`<div class="grow"><strong>${esc(jobName(s))}</strong><span>${esc(sourceLabel(s))} • ${esc(formatDate(s.updatedAt||s.createdAt))}${group.count>1?` • ${group.count} çalışma kaydı`:""}</span></div><span class="status-pill ${info.tone}">${esc(info.label)}</span>`;row.addEventListener("click",async()=>{state.selectedId=s.sessionId;renderSessionPicker();await loadSelectedSummary();});box.append(row);}}
function normalizeArchiveCaseId(value){const raw=String(value||"").trim(),m=raw.match(/^(20\d{2})[_/-](\d{1,10})$/);return m?`${m[1]}/${m[2]}`:raw.replace(/_/g,"/");}
function archiveCaseKey(item){return[String(item?.institutionName||item?.courtName||"").trim().toLocaleLowerCase("tr-TR"),normalizeArchiveCaseId(item?.caseId||"").toLocaleLowerCase("tr-TR")].join("|");}
function archiveCaseEntries(){return(Array.isArray(state.archiveRegistry?.caseIndex)?state.archiveRegistry.caseIndex:[]).map(item=>({...item,key:archiveCaseKey(item)})).filter(item=>item.key!=="|");}
function selectedArchiveCase(){return state.selectedArchiveKey?archiveCaseEntries().find(item=>item.key===state.selectedArchiveKey)||null:null;}
function archiveCaseDescriptor(d){const parts=String(d?.targetPath||"").replace(/\\/g,"/").split("/").filter(Boolean),court=String(d?.courtName||parts[0]||"UYAP Dosyası").trim(),caseId=normalizeArchiveCaseId(d?.caseId||String(parts[1]||"").replace(/_(?:Dosya|Esas)$/i,""));let title=String(d?.caseTitle||parts[2]||caseId||"Dava Dosyası").trim();title=title.replace(/^(20\d{2})_(\d{1,10})(?=\b|\s|\()/,(_,year,no)=>`${year}/${no}`);if(caseId&&!title.includes(caseId)&&!/^20\d{2}\/\d+/.test(title))title=`${caseId} • ${title}`;return{key:[court.toLocaleLowerCase("tr-TR"),caseId||title.toLocaleLowerCase("tr-TR")].join("|"),title,court,caseId};}
function recentArchiveCaseGroups(limit=12){const indexed=Array.isArray(state.archiveRegistry?.caseIndex)?state.archiveRegistry.caseIndex:[];if(indexed.length)return indexed.slice(0,limit).map((g,index)=>({key:`${g.institutionName||""}|${g.caseId||index}`,title:g.caseTitle||g.caseId||"Dava/İcra Dosyası",court:g.institutionName||"Bilinmeyen Kurum/Daire",caseId:normalizeArchiveCaseId(g.caseId),count:n(g.documentCount),verified:n(g.verifiedCount),physical:n(g.physicalReadyCount??g.presentCount),metadataVerified:n(g.metadataVerifiedCount??g.verifiedCount),problem:n(g.problemCount),missing:n(g.missingCount),sizeMismatch:n(g.sizeMismatchCount),staleMetadata:n(g.staleMetadataCount),totalBytes:n(g.totalBytes),when:g.latestAt||null,sourceSystem:g.sourceSystem||null}));const docs=(state.archiveRegistry?.documents||[]).filter(d=>d?.status==="archived"),groups=new Map();for(const d of docs){const info=archiveCaseDescriptor(d),when=d.fileModifiedAt||d.updatedAt||null;let group=groups.get(info.key);if(!group){group={...info,when,count:0,verified:0,problem:0,missing:0};groups.set(info.key,group);}group.count+=1;group.verified+=1;if((new Date(when||0).getTime()||0)>(new Date(group.when||0).getTime()||0))group.when=when;}return[...groups.values()].sort((a,b)=>(new Date(b.when||0).getTime()||0)-(new Date(a.when||0).getTime()||0)).slice(0,limit);}
function renderRecentArchiveDocs(){const box=$("recentArchiveDocs");if(!box)return;box.textContent="";const groups=recentArchiveCaseGroups(12);if(!groups.length){box.innerHTML='<div class="empty-state">Yerel arşivde gösterilecek dava dosyası bulunamadı.</div>';return;}for(const group of groups){const row=document.createElement("div");row.className="compact-row";const status=group.problem?`${group.problem} sorun`:`${group.verified||group.count} doğrulandı`;row.innerHTML=`<div class="grow"><strong>${esc(group.court)} • ${esc(group.caseId||group.title)}</strong><span>${esc(`${group.count} evrak • ${status} • ${formatBytes(group.totalBytes)} • ${formatDate(group.when)}`)}</span></div><span class="status-pill ${group.problem?"warn":"good"}">${group.problem?"Kontrol":"Dosya"}</span>`;row.addEventListener("click",()=>showView("archive"));box.append(row);}}
function renderOverviewHealth(){const box=$("overviewHealth");const s=state.summary,r=state.archiveRegistry;const pending=n(r?.partialFiles)+n(r?.reviewFiles);const localArchiveValue=r?.archiveExists?`${n(r.presentFiles).toLocaleString("tr-TR")} indi${pending?` • ${pending.toLocaleString("tr-TR")} kontrol`:""}`:"Bulunamadı";const matchState=String(s?.localArchiveScanState||"");const matched=n(s?.localArchiveRecognizedCount),ambiguous=n(s?.localArchiveAmbiguousCount),missing=n(s?.localArchiveUnmatchedCount);const activeMatchValue=!s?"İş bekleniyor":matchState==="complete"?`${matched.toLocaleString("tr-TR")} bulundu${ambiguous?` • ${ambiguous.toLocaleString("tr-TR")} belirsiz`:""}${missing?` • ${missing.toLocaleString("tr-TR")} bulunamadı`:""}`:matchState==="unavailable"?"Kontrol yapılamadı":"Bekliyor";const rows=[{label:"Uzantı",value:`v${VERSION}`,tone:"good"},{label:"Yerel ajan",value:state.agent?.ok?`v${state.agent.version}`:"Kontrol gerekli",tone:state.agent?.ok?"good":state.agent===null?"warn":"bad"},{label:"Yerel UYAP Arşiv",value:localArchiveValue,tone:r?.archiveExists?"good":"warn"},{label:"Aktif iş • yerel eşleşme",value:activeMatchValue,tone:matchState==="complete"?(ambiguous?"warn":"good"):s?"warn":"warn"},{label:"UYAP kaynağı",value:s?sourceLabel(s):"İş bekleniyor",tone:s?"good":"warn"},{label:"Final bütünlük",value:s?.finalIntegrity?.gatePassed?"Doğrulandı":s?.status==="incomplete"?"Kontrol gerekli":"Bekliyor",tone:s?.finalIntegrity?.gatePassed?"good":s?.status==="incomplete"?"bad":"warn"}];box.textContent="";for(const row of rows){const d=document.createElement("div");d.className="health-row";d.innerHTML=`<span class="check-dot ${row.tone}"></span><div><strong>${esc(row.label)}</strong><span>${esc(row.value)}</span></div>`;box.append(d);}}

function renderJobs(){
  const tbody=$("jobsRows"),query=$("jobSearch")?.value?.trim().toLocaleLowerCase("tr-TR")||"",status=$("jobStatusFilter")?.value||"",groups=groupedSessions();
  const rows=groups.filter(({session:s})=>(!status||s.status===status)&&(!query||`${jobName(s)} ${sourceLabel(s)}`.toLocaleLowerCase("tr-TR").includes(query)));
  const sessionKeys=new Set(groups.map(({session:s})=>archiveCaseKey({institutionName:s.institutionName||s.courtName,caseId:s.caseId}))),localRows=archiveCaseEntries().filter(item=>!sessionKeys.has(item.key)&&!status&&(!query||`${item.institutionName||""} ${item.caseId||""} ${item.caseTitle||""}`.toLocaleLowerCase("tr-TR").includes(query)));
  const totalSessions=state.sessions.length,completed=state.sessions.filter(s=>s.status==="completed").length,attention=state.sessions.filter(s=>s.status==="incomplete"||n(s.counts?.review_required)+n(s.counts?.failed)>0).length+localRows.filter(item=>n(item.problemCount)>0).length;
  $("jobsSummaryStrip").innerHTML=`<span><strong>${groups.length+localRows.length}</strong> benzersiz dosya</span><span><strong>${totalSessions}</strong> çalışma kaydı</span><span><strong>${localRows.length}</strong> yerel arşivden kurtarıldı</span><span class="${attention?"bad-text":""}"><strong>${attention}</strong> kontrol bekliyor</span>`;
  tbody.textContent="";$("jobsEmpty").hidden=rows.length+localRows.length>0;
  for(const group of rows){const s=group.session,tr=document.createElement("tr"),info=statusInfo(s),total=n(s.total),done=verifiedFor(s),pct=total?Math.min(100,Math.round(done/total*100)):0;tr.innerHTML=`<td><span class="cell-title">${esc(jobName(s))}</span><span class="cell-sub">${group.count>1?`${group.count} çalışma kaydı • `:""}${esc(s.sessionId)}</span></td><td>${esc(sourceLabel(s))}</td><td><span class="status-pill ${info.tone}">${esc(info.label)}</span></td><td><div class="job-progress"><span>${done.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")}</span><div><i style="width:${pct}%"></i></div></div></td><td>${esc(formatDate(s.updatedAt||s.createdAt))}</td><td><button class="row-action" data-open-job="${esc(s.sessionId)}">Aç</button></td>`;tbody.append(tr);}tbody.querySelectorAll("[data-open-job]").forEach(b=>b.addEventListener("click",async()=>{state.selectedId=b.dataset.openJob;renderSessionPicker();await loadSelectedSummary();showView("overview");}));
  for(const item of localRows){const tr=document.createElement("tr"),total=n(item.documentCount),physical=n(item.physicalReadyCount??item.presentCount),pct=total?Math.min(100,Math.round(physical/total*100)):0;tr.innerHTML=`<td><span class="cell-title">${esc(item.institutionName||"Bilinmeyen Kurum/Daire")} • ${esc(normalizeArchiveCaseId(item.caseId)||item.caseTitle||"Dava dosyası")}</span><span class="cell-sub">Tarayıcı oturumu yok • yerel _archive.db kaydı</span></td><td>${esc(item.sourceSystem==="UYAP_KURUM"?"UYAP Kurum":item.sourceSystem||"Yerel arşiv")}</td><td><span class="status-pill warn">Yerel arşiv</span></td><td><div class="job-progress"><span>${physical.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")}</span><div><i style="width:${pct}%"></i></div></div></td><td>${esc(formatDate(item.latestAt))}</td><td><button class="row-action" data-open-archive="${esc(encodeURIComponent(item.key))}">Aç</button></td>`;tbody.append(tr);}tbody.querySelectorAll("[data-open-archive]").forEach(b=>b.addEventListener("click",async()=>{state.selectedId=null;state.selectedArchiveKey=decodeURIComponent(b.dataset.openArchive);renderSessionPicker();await loadSelectedSummary();showView("overview");}));
}


function renderAiDock(s){
  const title=$("aiDockTitle"),text=$("aiDockText");if(!title||!text)return;
  if(!s){title.textContent="Dosya seçilmedi";text.textContent="Bir dosya seçildiğinde yerel arşiv bağlamı AI için hazırlanacak.";return;}
  title.textContent=jobName(s);const total=n(s.total),done=verifiedFor(s);const waiting=Math.max(0,total-done);text.textContent=waiting?`AI KAPALI — dosya tamamlanmamış. ${done.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} evrak doğrulandı; ${waiting.toLocaleString("tr-TR")} evrak bekliyor. AI sorgusu dosya tamamlanınca açılacak.`:`${done.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} evrak yerel bağlamda. Dosyanın AI uygunluğu ayrıca denetlenir.`;
}
function documentTitle(d){return d?.documentTitle||d?.displayName||d?.originalFilename||"Adsız evrak";}
function documentType(d){return String(d?.extension||d?.contentKind||"").replace(/^\./,"").toUpperCase()||"DOSYA";}
function normalizedDocDate(d){const raw=d?.documentDate||d?.verifiedAt||d?.completedAt||d?.updatedAt||null;if(!raw)return null;const dt=new Date(raw);return Number.isNaN(dt.getTime())?null:dt;}
function shortDate(value){const d=value instanceof Date?value:new Date(value||0);return Number.isNaN(d.getTime())?"-":new Intl.DateTimeFormat("tr-TR",{day:"2-digit",month:"2-digit",year:"numeric"}).format(d);}
async function renderTimeline(){
  const title=$("timelineTitle"),list=$("timelineList"),empty=$("timelineEmpty");if(!title||!list)return;
  if(!state.selectedId||!state.summary){title.textContent="Zaman Çizelgesi";list.textContent="";empty.hidden=false;return;}
  title.textContent=jobName(state.summary);list.innerHTML='<div class="empty-state">Zaman çizelgesi hazırlanıyor…</div>';
  try{const report=await ensureReport();const docs=Array.isArray(report?.documents)?report.documents:[];state.timelineDocs=docs;renderTimelineRows();}catch(e){state.timelineDocs=[];list.textContent="";empty.hidden=false;empty.textContent=String(e).replace(/^Error:\s*/,"")||"Zaman çizelgesi oluşturulamadı.";}
}
function renderTimelineRows(){
  const docs=Array.isArray(state.timelineDocs)?state.timelineDocs:[],q=$("timelineSearch")?.value.trim().toLocaleLowerCase("tr-TR")||"",type=$("timelineType")?.value||"";
  const types=[...new Set(docs.map(documentType).filter(Boolean))].sort();const sel=$("timelineType"),prev=sel.value;sel.innerHTML='<option value="">Tüm türler</option>'+types.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join("");sel.value=types.includes(prev)?prev:"";
  const dated=docs.map(d=>({d,date:normalizedDocDate(d)})).filter(x=>x.date).sort((a,b)=>b.date-a.date);const filtered=dated.filter(({d})=>(!type||documentType(d)===type)&&(!q||`${documentTitle(d)} ${d.caseId||""} ${documentType(d)}`.toLocaleLowerCase("tr-TR").includes(q)));
  $("tlTotal").textContent=dated.length.toLocaleString("tr-TR");$("tlTypes").textContent=types.length.toLocaleString("tr-TR");$("tlFirst").textContent=dated.length?shortDate(dated[dated.length-1].date):"-";$("tlLast").textContent=dated.length?shortDate(dated[0].date):"-";
  const list=$("timelineList");list.textContent="";$("timelineEmpty").hidden=filtered.length>0;
  let lastDay="";for(const {d,date} of filtered){const day=shortDate(date),row=document.createElement("div");row.className="timeline-item";row.innerHTML=`<div class="timeline-rail"><span></span></div><div class="timeline-date">${day!==lastDay?esc(day):""}</div><div class="timeline-content"><div class="timeline-title-row"><strong>${esc(documentTitle(d))}</strong><span>${esc(documentType(d))}</span></div><p>${esc(d.uyapPath||d.targetPath||d.localPath||"Arşiv kaydı")}</p><div class="timeline-meta"><span>${esc(archiveRowStatusLabel(d.status))}</span><span>${esc(d.sourceSystem==="UYAP_KURUM"?"UYAP Kurum":"UYAP")}</span></div></div>`;list.append(row);lastDay=day;}
}
function setAiPrompt(prompt,{navigate=true,workflowId="ask"}={}){state.aiWorkflowId=workflowId;state.aiPrompt=String(prompt||"");if($("aiPrompt"))$("aiPrompt").value=state.aiPrompt;if($("aiPromptPreview"))$("aiPromptPreview").textContent=state.aiPrompt?`Hazır komut: “${state.aiPrompt}”`:`Önerilen bir işlemi seçin veya aşağıya sorunuzu yazın.`;updateAiSendButton();if(navigate)showView("ai");}
// "Önemli Tarihler", "Ara Kararlar ve Yapılacaklar", "Taraflar" and "Tebligatlar" are
// read from the records by rules on the agent; they need the case index but no AI provider.
const AI_RULE_WORKFLOWS={important_dates:"Önemli tarihleri çıkar",court_orders:"Ara kararları göster",parties:"Tarafları göster",tebligat:"Tebligatları göster"};
function aiWorkflowNeedsProvider(){return !AI_RULE_WORKFLOWS[state.aiWorkflowId];}
function aiSendLabel(){return AI_RULE_WORKFLOWS[state.aiWorkflowId]||(isCaseSummaryPrompt(state.aiPrompt)?"Dosya özetini oluştur":"Soruyu gönder");}
function updateAiSendButton(){const send=$("aiSend"),gate=state.aiSendGate;if(!send||!gate)return;const providerOk=gate.providerReady||!aiWorkflowNeedsProvider();send.disabled=!gate.caseReady||!providerOk||!gate.indexReady;send.textContent=!gate.caseReady?(gate.caseSelected?"AI KAPALI — dosya tamamlanmamış":"Önce yerel dava dosyası seçin"):!providerOk?"AI sağlayıcısı bağlanınca kullanılabilir":(gate.indexReady?aiSendLabel():"Önce tam dava indeksini hazırla");}
function selectedAiArchiveCase(){return state.aiCases.find(item=>archiveCaseKey(item)===state.aiSelectedCaseKey)||null;}
const AI_WORKFLOW_TITLES={case_summary:"Dosya Özeti",important_dates:"Önemli Tarihler",court_orders:"Ara Kararlar ve Yapılacaklar",new_updates:"Yeni Ne Var?",parties:"Taraflar",tebligat:"Tebligatlar",classification:"Akıllı Sınıflandırma",chronology:"Kronoloji",ask:"Dosyada Sor"};
function isCaseSummaryPrompt(){return state.aiWorkflowId==="case_summary";}
function normalizeSummaryHeading(value){
  return String(value||"")
    .replace(/^#{1,6}\s*/,"")
    .replace(/^\*\*(.*?)\*\*:?$/,"$1")
    .replace(/^__(.*?)__:?$/,"$1")
    .replace(/:$/,"")
    .trim()
    .toLocaleLowerCase("tr-TR");
}
// case_summary_v2 headings (rule-based facts + validated narrative) and the
// four legacy v1 headings, so saved summaries of both kinds keep rendering.
const CASE_SUMMARY_SECTIONS=[
  {key:"kunye",title:"Künye",list:true,aliases:["künye","kunye"]},
  {key:"lastHearing",title:"Son celse ve ara kararlar",list:true,aliases:["son celse ve ara kararlar"]},
  {key:"next",title:"Sonraki duruşma ve süreler",list:true,highlight:true,aliases:["sonraki duruşma ve süreler","sonraki durusma ve sureler"]},
  {key:"overview",title:"Genel durum",list:false,aliases:["genel durum"]},
  {key:"developments",title:"Öne çıkan gelişmeler",list:true,aliases:["öne çıkan gelişmeler","one cikan gelismeler"]},
  {key:"timeline",title:"Akış",list:true,aliases:["akış","akis"]},
  {key:"coverage",title:"Kapsam ve eksikler",list:true,aliases:["kapsam ve eksikler"]},
  {key:"topic",title:"Dosyanın konusu",list:false,aliases:["dosyanın konusu","dosyanin konusu"]},
  {key:"status",title:"Son durum",list:false,aliases:["son durum"]},
  {key:"dates",title:"Önemli tarihler",list:true,aliases:["önemli tarihler","onemli tarihler"]}
];
function summaryTextWithRefs(value){return esc(value).replace(/\[(D\d{6})\]/g,'<span class="ai-ref">$1</span>');}
function parseCaseSummary(answer){
  const aliases=new Map(CASE_SUMMARY_SECTIONS.flatMap(section=>section.aliases.map(alias=>[alias,section.key])));
  const sections=Object.fromEntries([...CASE_SUMMARY_SECTIONS.map(section=>[section.key,[]]),["other",[]]]);
  let current="other";
  for(const raw of String(answer||"").split(/\r?\n/)){
    const line=raw.trim();
    if(!line)continue;
    const heading=normalizeSummaryHeading(line);
    if(aliases.has(heading)){current=aliases.get(heading);continue;}
    const cleaned=line.replace(/^[-•*]\s*/,"").replace(/^\d+[.)]\s*/,"").replace(/^\*\*(.*?)\*\*$/,"$1").trim();
    if(cleaned)sections[current].push(cleaned);
  }
  return sections;
}
function renderCaseSummaryHtml(answer){
  const sections=parseCaseSummary(answer);
  const known=CASE_SUMMARY_SECTIONS.map(section=>{
    const items=sections[section.key];
    if(!items?.length)return "";
    const content=section.list
      ?`<ul>${items.slice(0,16).map(item=>`<li>${summaryTextWithRefs(item)}</li>`).join("")}</ul>`
      :items.slice(0,4).map(item=>`<p>${summaryTextWithRefs(item)}</p>`).join("");
    return `<section class="ai-summary-section${section.highlight?" is-key":""}"><h3>${esc(section.title)}</h3>${content}</section>`;
  }).join("");
  return `<div class="ai-summary-document">${known||`<section class="ai-summary-section"><p>${esc(answer||"Yanıt alınamadı.")}</p></section>`}</div>`;
}
function renderDateRefsHtml(item){
  return (Array.isArray(item?.refs)?item.refs:[]).map(ref=>`<span class="ai-ref">${esc(ref)}</span>`).join("")+(item?.moreRefs?`<span class="ai-ref more">+${esc(item.moreRefs)}</span>`:"");
}
// Sections produced by the agent's rule-based views (Önemli Tarihler, Ara
// Kararlar ve Yapılacaklar): dated rows with optional addressee, status badge
// and related-record lines, every fact tagged with its [Dxxxxxx] source.
function renderRuleSectionsHtml(sections){
  const tone=value=>["good","warn","bad","info","muted"].includes(value)?value:"muted";
  const block=section=>{
    const items=Array.isArray(section?.items)?section.items:[];
    const rows=items.length
      ?items.map(item=>{
        const who=item.who?`<b class="ai-order-who">${esc(item.who)}</b> — `:"";
        const status=item.status?.label?`<span class="ai-order-status tone-${tone(item.status.tone)}">${esc(item.status.label)}</span>`:"";
        const lines=(Array.isArray(item.lines)?item.lines:[]).map(line=>`<li class="tone-${tone(line.tone)}">${esc(line.text||"")}${renderDateRefsHtml(line)}</li>`).join("");
        const main=item.kind==="party"?`<b class="ai-party-name">${esc(item.text||"")}</b>`:esc(item.text||"");
        return `<li class="ai-date-row kind-${esc(item.kind||"event")}${item.isNew?" is-new":""}"><span class="ai-date">${esc(item.dateText||"")}</span><span class="ai-date-text">${item.isNew?'<span class="ai-new-badge">YENİ</span>':""}${who}${main}${status}${renderDateRefsHtml(item)}${lines?`<ul class="ai-order-lines">${lines}</ul>`:""}</span></li>`;
      }).join("")
      :`<li class="ai-date-row empty"><span class="ai-date"></span><span class="ai-date-text">${esc(section?.empty||"Kayıt yok.")}</span></li>`;
    const refs=Array.isArray(section?.refs)&&section.refs.length?renderDateRefsHtml({refs:section.refs}):"";
    return `<section class="ai-summary-section ai-dates-section${section?.highlight?" is-key":""}"><h3>${esc(section?.title||"")}${refs}${section?.subtitle?`<small>${esc(section.subtitle)}</small>`:""}</h3><ul class="ai-date-list">${rows}</ul></section>`;
  };
  return `<div class="ai-summary-document ai-dates-document">${(Array.isArray(sections)?sections:[]).map(block).join("")}</div>`;
}
function renderImportantDatesHtml(sections){return renderRuleSectionsHtml(sections);}
function ruleSectionsOf(result){
  if(result?.datesSchema==="important_dates_v2"&&Array.isArray(result?.dateSections))return result.dateSections;
  if(result?.ordersSchema==="court_orders_v1"&&Array.isArray(result?.orderSections))return result.orderSections;
  if(result?.partiesSchema==="parties_v1"&&Array.isArray(result?.partySections))return result.partySections;
  if(result?.tebligatSchema==="tebligat_v1"&&Array.isArray(result?.tebligatSections))return result.tebligatSections;
  return null;
}
function renderSummarySourcesHtml(sources){
  const rows=Array.isArray(sources)?sources:[];
  if(!rows.length)return "";
  return '<div class="ai-summary-source-grid"><strong>Özet için seçilen evraklar</strong>'+rows.map(source=>
    '<div class="ai-summary-source-card"><span>'+esc(source.sourceRef||"-")+'</span><div><b>'+esc(source.title||"Adsız evrak")+'</b><small>'+esc(source.selectionReason||"Seçili doğrulanmış kaynak")+(source.documentDate?' • '+esc(shortDate(source.documentDate)):"")+'</small></div></div>'
  ).join("")+'</div>';
}
function renderAiResult(result,selected,{fromHistory=false}={}){
  const panel=$("aiResultPanel"),content=$("aiResultContent"),title=$("aiResultTitle"),meta=$("aiResultMeta"),pdf=$("aiSummaryPdf"),saved=$("aiSummarySavedBadge");
  if(!panel||!content)return;
  // A saved rule view also carries a summaryId; only the summary itself is summaryMode.
  const summaryMode=result?.retrievalMode==="case_summary_v1"||(Boolean(result?.summaryId)&&savedWorkflow(result)==="case_summary");
  panel.hidden=false;
  state.aiCurrentResult={...result,archiveCase:result?.archiveCase||selected||null};
  title.textContent=summaryMode?"Dosya Özeti":(result?.workflowTitle||AI_WORKFLOW_TITLES[result?.workflowId]||"Dosyada Sor Yanıtı");
  const refs=Array.isArray(result?.sourceRefs)?result.sourceRefs:[],sources=Array.isArray(result?.sources)?result.sources:[];
  const created=result?.createdAt||result?.savedSummary?.createdAt||null;
  const parts=[result?.provider||"AI",result?.model||"",summaryMode&&sources.length?`${sources.length} seçili evrak`:refs.length?`${refs.length} kaynak`:"",created?formatDate(created):""].filter(Boolean);
  meta.textContent=parts.join(" • ")||"Yerel AI sonucu";
  const workflowNote=result?.workflowNotice
    ?`<div class="ai-prompt-preview">${esc(result.workflowNotice)}</div>`:"";
  const ruleSections=ruleSectionsOf(result);
  content.innerHTML=workflowNote+(summaryMode
    ?renderCaseSummaryHtml(result?.answer||"")+renderSummarySourcesHtml(sources)
    :ruleSections
      ?renderRuleSectionsHtml(ruleSections)
      :`<div class="ai-freeform-answer">${esc(result?.answer||"Yanıt alınamadı.")}</div>`);
  if(pdf)pdf.hidden=!(summaryMode||(ruleSections&&SAVED_PDF_WORKFLOWS.has(result?.workflowId)));
  if(saved){const locallySaved=(summaryMode||Boolean(ruleSections))&&(fromHistory||Boolean(result?.savedSummary)||Boolean(result?.summaryId));saved.hidden=!locallySaved;saved.textContent=locallySaved?"✓ Yerel arşive kaydedildi":"";}
  panel.scrollIntoView({behavior:"smooth",block:"start"});
}
function renderAiResultLoading(summaryMode){
  const panel=$("aiResultPanel"),content=$("aiResultContent"),title=$("aiResultTitle"),meta=$("aiResultMeta"),pdf=$("aiSummaryPdf"),saved=$("aiSummarySavedBadge");
  if(!panel||!content)return;
  panel.hidden=false;
  if(title)title.textContent=summaryMode?"Dosya Özeti hazırlanıyor":(AI_WORKFLOW_TITLES[state.aiWorkflowId]||"Dosyada Sor");
  if(meta)meta.textContent=summaryMode?"Önemli evraklar kurallarla seçiliyor…":"Yerel dava arşivinde kaynaklar seçiliyor…";
  if(pdf)pdf.hidden=true;if(saved)saved.hidden=true;
  content.innerHTML='<div class="ai-result-loading">✦ '+(summaryMode?"Kısa ve okunabilir dosya özeti hazırlanıyor…":"Kaynaklı yanıt hazırlanıyor…")+'</div>';
}
function renderAiResultError(error){
  const panel=$("aiResultPanel"),content=$("aiResultContent"),title=$("aiResultTitle"),meta=$("aiResultMeta"),pdf=$("aiSummaryPdf"),saved=$("aiSummarySavedBadge");
  if(!panel||!content)return;
  panel.hidden=false;if(title)title.textContent="AI işlemi tamamlanamadı";if(meta)meta.textContent="Tanı bilgisi hata mesajında korunur.";if(pdf)pdf.hidden=true;if(saved)saved.hidden=true;
  content.innerHTML='<div class="ai-result-error">'+esc(String(error).replace(/^Error:\s*/,""))+'</div>';
}
// Saved results: Dosya Özeti and the rule views share one local history.
// PDF is offered for the summary, Önemli Tarihler, Ara Kararlar and Tebligatlar.
const SAVED_PDF_WORKFLOWS=new Set(["case_summary","important_dates","court_orders","tebligat"]);
const SAVED_FILTERS=[["all","Tümü"],["case_summary","Dosya Özeti"],["important_dates","Önemli Tarihler"],["court_orders","Ara Kararlar"],["parties","Taraflar"],["tebligat","Tebligatlar"]];
const RULE_SCHEMA_KEYS={important_dates:["datesSchema","dateSections"],court_orders:["ordersSchema","orderSections"],parties:["partiesSchema","partySections"],tebligat:["tebligatSchema","tebligatSections"]};
const AI_UNDO_MS=10000;
const savedWorkflow=item=>item?.workflowId||"case_summary";
function renderAiSummaryHistory(){
  const box=$("aiSummaryHistory"),count=$("aiSummaryHistoryCount"),filters=$("aiSavedFilters");
  if(!box)return;
  const all=Array.isArray(state.aiSummaries)?state.aiSummaries:[];
  const filter=SAVED_FILTERS.some(([key])=>key===state.aiSavedFilter)?state.aiSavedFilter:"all";
  const rows=filter==="all"?all:all.filter(item=>savedWorkflow(item)===filter);
  if(count)count.textContent=`${all.length.toLocaleString("tr-TR")} kayıt`;
  if(filters)filters.innerHTML=all.length?SAVED_FILTERS.map(([key,label])=>{
    const total=key==="all"?all.length:all.filter(item=>savedWorkflow(item)===key).length;
    return total||key===filter?`<button class="ai-saved-filter${key===filter?" is-active":""}" data-ai-saved-filter="${key}">${esc(label)} <span>${total}</span></button>`:"";
  }).join(""):"";
  const undo=state.aiUndo&&state.aiUndo.until>Date.now()
    ?`<div class="ai-history-undo"><span>${esc(state.aiUndo.label)} silindi.</span><button data-ai-saved-undo="${esc(state.aiUndo.summaryId)}">Geri al</button></div>`:"";
  if(!rows.length){box.innerHTML=undo+`<div class="ai-history-empty">${all.length?"Bu türde kaydedilmiş sonuç yok.":"Bu dava dosyası için henüz kaydedilmiş sonuç yok."}</div>`;return;}
  box.innerHTML=undo+rows.map(item=>{
    const id=esc(item.summaryId||""),workflow=savedWorkflow(item),title=item.workflowTitle||AI_WORKFLOW_TITLES[workflow]||"Dosya Özeti";
    const sourceCount=Array.isArray(item.sources)?item.sources.length:0;
    const meta=workflow==="case_summary"?`${item.model||"Yerel AI"} • ${sourceCount} kaynak`:`Yerel kurallar • ${(item.sourceRefs||[]).length} kaynak evrak`;
    if(state.aiDeleteConfirm===item.summaryId){
      return `<div class="ai-history-item is-confirm"><div><strong>Bu kayıt silinsin mi?</strong><small>${esc(title)} • ${esc(formatDate(item.createdAt))}. Arşivdeki evraklar ve indirdiğiniz PDF'ler etkilenmez.</small></div><div class="ai-history-actions"><button class="ai-history-danger" data-ai-saved-delete-confirm="${id}">Evet, sil</button><button data-ai-saved-delete-cancel="${id}">Vazgeç</button></div></div>`;
    }
    const pdf=SAVED_PDF_WORKFLOWS.has(workflow)?`<button data-ai-saved-pdf="${id}">PDF</button>`:"";
    return `<div class="ai-history-item"><div><span class="ai-history-type type-${esc(workflow)}">${esc(title)}</span><strong>${esc(formatDate(item.createdAt))}</strong><small>${esc(meta)}</small></div><div class="ai-history-actions"><button data-ai-saved-open="${id}">Aç</button>${pdf}<button class="ai-history-delete" data-ai-saved-delete="${id}" title="Kaydı sil">Sil</button></div></div>`;
  }).join("");
}
async function loadAiSummaryHistory(selected){
  const requestId=++aiHistoryRequestId;
  if(!selected){state.aiSummaries=[];renderAiSummaryHistory();return;}
  const selectedKey=archiveCaseKey(selected);
  const stillCurrent=()=>requestId===aiHistoryRequestId&&selectedKey===state.aiSelectedCaseKey&&state.view==="ai";
  const box=$("aiSummaryHistory"),count=$("aiSummaryHistoryCount");
  if(box)box.innerHTML='<div class="ai-history-empty">Kaydedilen sonuçlar yükleniyor…</div>';
  let timeoutId;
  try{
    // Read-only UI timeout: the pending native request is not cancelled.
    // Do not use this time limit for archive scans, integrity or AI execution.
    const payload=await Promise.race([
      sendRuntime(MESSAGE.GET_AI_ARCHIVE_SUMMARIES,{institutionName:selected.institutionName,caseId:selected.caseId,limit:60}),
      new Promise((_,reject)=>{timeoutId=setTimeout(()=>reject(new Error("Kayıt geçmişi sorgusu gecikti; bu sayfayı yeniden açarak tekrar deneyin.")),AI_HISTORY_DISPLAY_TIMEOUT_MS);})
    ]);
    if(!stillCurrent())return;
    state.aiSummaries=Array.isArray(payload?.summaries)?payload.summaries:[];
    renderAiSummaryHistory();
  }catch(error){
    if(!stillCurrent())return;
    state.aiSummaries=[];
    if(count)count.textContent="Yüklenemedi";
    if(box)box.innerHTML='<div class="ai-history-empty">Kayıt geçmişi okunamadı: '+esc(String(error).replace(/^Error:\s*/,""))+'</div>';
  }finally{clearTimeout(timeoutId);}
}

// A listed rule-view record carries no sections (list stays small); fetch it whole.
async function loadSavedResult(item,selected){
  if(savedWorkflow(item)==="case_summary"||Array.isArray(item.sections))return item;
  const payload=await sendRuntime(MESSAGE.GET_AI_ARCHIVE_SUMMARY,{institutionName:selected.institutionName,caseId:selected.caseId,summaryId:item.summaryId});
  return payload?.summary||item;
}
function savedToResult(item,selected){
  const workflowId=savedWorkflow(item),base={...item,workflowId,archiveCase:selected};
  if(workflowId==="case_summary")return {...base,retrievalMode:"case_summary_v1"};
  const keys=RULE_SCHEMA_KEYS[workflowId];
  return keys?{...base,[keys[0]]:item.sectionsSchema,[keys[1]]:Array.isArray(item.sections)?item.sections:[]}:base;
}
async function openSummaryPrint(summary){
  const selected=summary?.archiveCase||selectedAiArchiveCase()||{};
  const sections=ruleSectionsOf(summary);
  const workflowId=sections?summary.workflowId:"case_summary";
  const documentTitle=sections?(summary.workflowTitle||AI_WORKFLOW_TITLES[workflowId]||"AI Sonucu"):"Dosya Özeti";
  const payload={
    schemaVersion:2,
    workflowId,
    title:"UYAP Archive AI - "+documentTitle,
    documentTitle,
    institutionName:summary?.institutionName||selected.institutionName||"",
    caseId:summary?.caseId||selected.caseId||"",
    createdAt:summary?.createdAt||summary?.savedSummary?.createdAt||new Date().toISOString(),
    answer:summary?.answer||"",
    sources:Array.isArray(summary?.sources)?summary.sources:[],
    sourceRefs:Array.isArray(summary?.sourceRefs)?summary.sourceRefs:[],
    model:summary?.model||"",
    provider:summary?.provider||"",
    sections:sections||null,
    workflowNotice:sections?(summary?.workflowNotice||""):""
  };
  await chrome.storage.local.set({[AI_PRINT_SUMMARY_KEY]:payload});
  await chrome.tabs.create({url:chrome.runtime.getURL("workspace/summary-print.html")});
}
// P2: a connected provider does not imply the selected court file is AI-ready.
// The existing Service Worker and Native Agent integrity gates remain unchanged.
function renderAiCaseAvailability(selected){
  const warning=$("aiIncompleteWarning"),details=$("aiIncompleteWarningDetails");
  const incomplete=!!selected&&selected.readyForAi!==true;
  if(warning){
    warning.hidden=!incomplete;
    if(incomplete&&details){
      const ready=Math.max(0,n(selected.physicalReadyDocuments));
      const total=Math.max(0,n(selected.totalDocuments));
      const remaining=Math.max(0,total-ready);
      const problems=Math.max(0,n(selected.problemDocuments));
      details.textContent=`${ready.toLocaleString("tr-TR")} / ${total.toLocaleString("tr-TR")} evrak fiziksel olarak hazır; ${remaining.toLocaleString("tr-TR")} kayıt henüz hazır değil${problems?` (${problems.toLocaleString("tr-TR")} kayıt kontrol gerektiriyor)`:""}. Dosya tamamlanana kadar AI indeksi, özet, sorgulama ve hazır analizler kullanılamaz. Mevcut evraklara Evraklar bölümünden ulaşabilirsiniz.`;
    }
  }
  // Never leave an answer from a previously selected case looking like a live AI result.
  if(!selected||incomplete){
    state.aiCurrentResult=null;
    const panel=$("aiResultPanel");if(panel)panel.hidden=true;
    const answer=$("aiAnswerBox");if(answer){answer.hidden=true;answer.textContent="";}
    const results=$("aiSearchResults");if(results)results.textContent="";
  }
  // Even the workflow shortcuts must not look actionable for incomplete files.
  for(const button of document.querySelectorAll("#view-ai [data-ai-workflow]")){
    button.disabled=!selected||incomplete;
    button.title=button.disabled?"AI kullanılamıyor: önce dava arşivi tamamlanmalı.":"";
  }
  const freeform=$("aiFreeformMode");if(freeform)freeform.disabled=!selected||incomplete;
  const prompt=$("aiPrompt");if(prompt)prompt.disabled=!selected||incomplete;
}

async function renderAiWorkspace({reuseCases=false}={}){
  const requestId=++aiWorkspaceRequestId;
  const ready=$("aiReadiness"),send=$("aiSend"),serviceLabel=$("aiServiceStatus"),indexLabel=$("aiIndexStatus"),buildButton=$("aiBuildIndex"),retrieveButton=$("aiRetrieve"),resultsBox=$("aiSearchResults"),answerBox=$("aiAnswerBox"),picker=$("aiArchiveCasePicker"),caseStatus=$("aiArchiveCaseStatus");
  // No old "ready" label or stale enabled action while a new case is loading.
  state.aiSendGate={caseSelected:false,caseReady:false,providerReady:false,indexReady:false};
  if(ready)ready.innerHTML='<span class="ai-ready-pill warn">• Dava dosyası AI uygunluğu kontrol ediliyor</span>';
  if(serviceLabel)serviceLabel.textContent="Dosya kontrol ediliyor";
  if(buildButton)buildButton.disabled=true;
  if(retrieveButton)retrieveButton.disabled=true;
  if(send){send.disabled=true;send.textContent="Dosya kontrol ediliyor";}
  renderAiCaseAvailability(null);
  try{
    // Switching between already listed cases must not rescan the archive.
    // Actual AI/index operations retain their fresh integrity checks.
    const haveDisplaySnapshot=reuseCases&&state.aiCases.length>0&&state.aiService;
    const [casePayload,service]=haveDisplaySnapshot
      ?[{cases:state.aiCases},state.aiService]
      :await Promise.all([
        sendRuntime(MESSAGE.GET_AI_ARCHIVE_CASES),
        sendRuntime(MESSAGE.GET_AI_SERVICE_STATUS)
      ]);
    if(requestId!==aiWorkspaceRequestId||state.view!=="ai")return;
    state.aiCases=Array.isArray(casePayload?.cases)?casePayload.cases:[];
    state.aiService=service;

    const activeCaseKey=state.summary?archiveCaseKey({institutionName:state.summary.institutionName||state.summary.courtName,caseId:state.summary.caseId}):null;
    if(!state.aiSelectedCaseKey||!state.aiCases.some(item=>archiveCaseKey(item)===state.aiSelectedCaseKey)){
      const activeMatch=activeCaseKey?state.aiCases.find(item=>archiveCaseKey(item)===activeCaseKey):null;
      const initial=activeMatch||state.aiCases.find(item=>item.readyForAi)||state.aiCases[0]||null;
      state.aiSelectedCaseKey=initial?archiveCaseKey(initial):null;
    }
    const selected=state.aiCases.find(item=>archiveCaseKey(item)===state.aiSelectedCaseKey)||null;

    if(picker){
      picker.textContent="";
      if(!state.aiCases.length){
        const option=document.createElement("option");option.value="";option.textContent="Yerel arşivde dava dosyası bulunamadı";picker.append(option);picker.disabled=true;
      }else{
        for(const item of state.aiCases){
          const option=document.createElement("option"),key=archiveCaseKey(item),readyCase=item.readyForAi===true;
          option.value=key;
          option.selected=key===state.aiSelectedCaseKey;
          option.textContent=`${readyCase?"✓":"⚠"} ${item.institutionName} • ${item.caseId} • ${n(item.physicalReadyDocuments)}/${n(item.totalDocuments)} evrak`;
          picker.append(option);
        }
        picker.disabled=false;
      }
    }

    const providerReady=service?.enabled===true&&service?.configured===true;
    renderAiCaseAvailability(selected);
    if(serviceLabel){const providerName=service?.provider==="openai"?"OpenAI":service?.provider==="lmstudio"?"LM Studio":"AI";serviceLabel.textContent=!selected?"AI KAPALI • Dosya seçilmedi":selected.readyForAi!==true?"AI KAPALI • Eksik arşiv":providerReady?`${providerName} • ${service?.model||"Bağlı"}`:"Sağlayıcı bekleniyor";}

    if(!selected){
      state.aiContext=null;state.aiIndex=null;
      $("aiCaseTitle").textContent="Yerel dava dosyası seçilmedi";
      $("aiCaseSubtitle").textContent="AI, indirme oturumundan bağımsız olarak tamamlanmış yerel dava arşivleri üzerinde çalışır.";
      $("aiSource").textContent="Yerel arşiv";
      $("aiDocCount").textContent="0";$("aiDateRange").textContent="-";
      ready.innerHTML='<span class="ai-ready-pill warn">• Arşiv dosyası bekleniyor</span><span class="ai-ready-pill warn">• Fiziksel bütünlük bekleniyor</span><span class="ai-ready-pill warn">• Yerel indeks bekleniyor</span><span class="ai-ready-pill '+(providerReady?"good":"warn")+'">'+(providerReady?"✓ AI sağlayıcısı bağlı":"• AI sağlayıcısı bekleniyor")+'</span>';
      if(caseStatus)caseStatus.textContent="Yerel UYAP_Arsiv içinde kayıtlı bir dava dosyası bulunamadı.";
      if(indexLabel)indexLabel.textContent="Önce tamamlanmış bir yerel dava dosyası seçin.";
      if(buildButton){buildButton.disabled=true;buildButton.textContent="Yerel AI indeksini hazırla";}
      if(retrieveButton)retrieveButton.disabled=true;
      if(send){send.disabled=true;send.textContent="Önce yerel dava dosyası seçin";}
      if(resultsBox)resultsBox.textContent="";
      if(answerBox){answerBox.hidden=true;answerBox.textContent="";}
      ++aiHistoryRequestId;
      state.aiSummaries=[];state.aiCurrentResult=null;renderAiSummaryHistory();
      if($("aiResultPanel"))$("aiResultPanel").hidden=true;
      return;
    }

    state.aiContext={kind:"UYAP_AI_ARCHIVE_CASE_CONTEXT",archiveCase:selected};
    void loadAiSummaryHistory(selected);
    $("aiCaseTitle").textContent=`${selected.institutionName} • ${selected.caseId}`;
    $("aiCaseSubtitle").textContent=`Yerel UYAP arşivi • ${selected.readyForAi?"tam fiziksel dosya":"eksik/kontrollü dosya"} • oturumdan bağımsız AI bağlamı`;
    $("aiSource").textContent="Yerel UYAP Arşiv";
    $("aiDocCount").textContent=n(selected.physicalReadyDocuments).toLocaleString("tr-TR");
    $("aiDateRange").textContent=selected.latestAt?`Son kayıt: ${formatDate(selected.latestAt)}`:"Tam arşiv";
    if(caseStatus){
      const orphanNote=n(selected.unregisteredPhysicalDocuments)>0
        ?` Ayrıca ${n(selected.unregisteredPhysicalDocuments).toLocaleString("tr-TR")} kayıt dışı fiziksel dosya var; bunlar AI kapsamına alınmaz ve dava tamlık hesabını etkilemez.`
        :"";
      caseStatus.textContent=selected.readyForAi
        ?`${n(selected.physicalReadyDocuments).toLocaleString("tr-TR")} / ${n(selected.totalDocuments).toLocaleString("tr-TR")} kayıtlı evrak fiziksel olarak hazır. Tam dava arşivi AI indekslemeye hazır; metin çıkarılabilen evraklar sorguya alınır.${orphanNote}`
        :`${n(selected.physicalReadyDocuments).toLocaleString("tr-TR")} / ${n(selected.totalDocuments).toLocaleString("tr-TR")} kayıtlı evrak hazır; ${n(selected.problemDocuments).toLocaleString("tr-TR")} kayıt kontrol gerektiriyor. Dosya tamamlanmadan AI sorgusu açılmaz.${orphanNote}`;
    }

    let indexStatus={ready:false,documents:0,chunks:0};
    // Do not start a second archive-integrity scan just to display an index
    // status that is unusable while any registered document is unresolved.
    // Building/querying the index remains blocked by the strict native gate.
    if(selected.readyForAi===true){
      try{
        indexStatus=await sendRuntime(MESSAGE.GET_AI_ARCHIVE_CASE_INDEX_STATUS,{institutionName:selected.institutionName,caseId:selected.caseId});
      }catch{}
    }
    if(requestId!==aiWorkspaceRequestId||state.view!=="ai")return;
    state.aiIndex=indexStatus;
    const indexReady=selected.readyForAi===true&&indexStatus?.ready===true;
    ready.innerHTML=selected.readyForAi===true
      ?`<span class="ai-ready-pill good">✓ Arşiv dosyası seçili</span><span class="ai-ready-pill good">✓ Tüm evraklar fiziksel hazır</span><span class="ai-ready-pill ${indexReady?"good":"warn"}">${indexReady?"✓ Yerel indeks hazır":"• Yerel indeks bekleniyor"}</span><span class="ai-ready-pill ${providerReady?"good":"warn"}">${providerReady?"✓ AI sağlayıcısı bağlı":"• AI sağlayıcısı bekleniyor"}</span>`
      :'<span class="ai-ready-pill warn">⚠ AI KULLANILAMIYOR • Dosya tamamlanmamış</span><span class="ai-ready-pill warn">• İndeksleme ve analiz kapalı</span>';
    // An index built before the full PDF reader left most PDFs unread.
    const rebuildNote=indexReady&&indexStatus?.rebuildRecommended===true
      ?"PDF okuma geliştirildi: daha önce okunamayan PDF'lerin de okunması için indeksi bir kez güncelleyin. ":"";
    if(indexLabel)indexLabel.textContent=indexReady
      ?`${rebuildNote}${n(indexStatus.documents).toLocaleString("tr-TR")} / ${n(selected.totalDocuments).toLocaleString("tr-TR")} evrakın metni AI indeksinde • ${n(indexStatus.chunks).toLocaleString("tr-TR")} parça. ${n(selected.totalDocuments)>n(indexStatus.documents)?`${n(selected.totalDocuments)-n(indexStatus.documents)} evrakın metni indekslenmemiş; görsel/OCR eksikliği, biçim veya kalite kontrolü gibi nedenler araştırılmalı. AI yalnız indeksli kaynaklara dayanır.`:""}`
      :selected.readyForAi?"Tam arşivden yerel metin indeksi henüz oluşturulmadı.":"AI KAPALI — dosyanın tüm evrakları doğrulanmadan indeks oluşturulamaz.";
    if(buildButton){buildButton.disabled=!selected.readyForAi;buildButton.textContent=!selected.readyForAi?"AI KAPALI — eksik evrak":rebuildNote?"İndeksi güncelle (PDF okuma)":indexReady?"Tam dava indeksini güncelle":"Tam dava indeksini hazırla";}
    if(retrieveButton){retrieveButton.disabled=!indexReady;retrieveButton.textContent=!selected.readyForAi?"AI KAPALI — eksik evrak":indexReady?"Yerel dava kaynaklarında ara":"Önce tam dava indeksini hazırla";}
    state.aiSendGate={caseSelected:true,caseReady:selected.readyForAi===true,providerReady,indexReady};updateAiSendButton();
  }catch(error){
    if(requestId!==aiWorkspaceRequestId||state.view!=="ai")return;
    renderAiCaseAvailability(null);
    if(serviceLabel)serviceLabel.textContent="AI KAPALI • Arşiv okunamadı";
    ++aiHistoryRequestId;
    state.aiCases=[];state.aiContext=null;state.aiIndex=null;state.aiSummaries=[];state.aiCurrentResult=null;renderAiSummaryHistory();
    $("aiCaseTitle").textContent="Yerel arşiv okunamadı";
    $("aiCaseSubtitle").textContent="AI için tamamlanmış dava dosyaları yüklenemedi.";
    $("aiSource").textContent="Yerel arşiv";
    $("aiDocCount").textContent="0";$("aiDateRange").textContent="-";
    ready.innerHTML='<span class="ai-ready-pill warn">• Arşiv dosyaları okunamadı</span><span class="ai-ready-pill warn">• Fiziksel bütünlük kontrol edilemedi</span><span class="ai-ready-pill warn">• Yerel indeks kontrol edilemedi</span><span class="ai-ready-pill warn">• AI bekliyor</span>';
    if(caseStatus)caseStatus.textContent=String(error).replace(/^Error:\s*/,"");
    if(indexLabel)indexLabel.textContent="Yerel dava arşivi hazır değil.";
    if(buildButton)buildButton.disabled=true;
    if(retrieveButton)retrieveButton.disabled=true;
    if(send){send.disabled=true;send.textContent="Yerel dava arşivi hazır değil";}
  }
  if($("aiPrompt"))$("aiPrompt").value=state.aiPrompt||"";
  if($("aiPromptPreview"))$("aiPromptPreview").textContent=state.aiPrompt?`Hazır komut: “${state.aiPrompt}”`:"Önerilen bir işlemi seçin veya aşağıya sorunuzu yazın.";
}

async function ensureReport(){if(state.report||!state.selectedId)return state.report;if(!state.selectedId)return null;state.report=await sendRuntime(MESSAGE.GET_REPORT_DATA,{sessionId:state.selectedId});return state.report;}
function archiveRowsForScope(){
  if($("archiveScope")?.value==="session")return Array.isArray(state.report?.documents)?state.report.documents:[];
  return Array.isArray(state.archivePageData)?state.archivePageData:[];
}
function archiveRowStatusLabel(status){
  if(status==="archived")return"Fiziksel arşivde";
  if(status==="verified")return"İndirildi ve doğrulandı";
  if(status==="skipped")return"Önceden yerelde";
  if(status==="queued")return"İndirilecek";
  if(status==="awaiting_browser")return"UYAP'tan bekleniyor";
  if(status==="downloading")return"İndiriliyor";
  if(status==="completed")return"Doğrulanıyor";
  if(status==="review"||status==="review_required")return"Kontrol gerekli";
  if(status==="retry_wait")return"Tekrar denenecek";
  if(status==="partial")return"Yarım kaldı";
  if(status==="missing")return"Fiziksel dosya eksik";
  if(status==="failed")return"Başarısız";
  return String(status||"-");
}
function archiveRowHasPhysicalFile(d){return ["archived","verified","skipped","review","partial"].includes(String(d?.status||""));}
function archiveRowTargetLabel(d){return archiveRowHasPhysicalFile(d)?(d?.targetPath||d?.actualFilename||"Yerel dosya"):"Henüz indirilmedi";}
function renderArchiveCaseIndexTable(){
  const panel=$("archiveCaseIndexPanel"),tbody=$("archiveCaseIndexRows"),count=$("archiveCaseIndexCount"),warning=$("archiveCaseIndexWarning");
  if(!panel||!tbody)return;
  const scope=$("archiveScope")?.value||"local";panel.hidden=scope!=="local";if(panel.hidden)return;
  const q=$("archiveSearch")?.value.trim().toLocaleLowerCase("tr-TR")||"";
  const rows=(Array.isArray(state.archiveRegistry?.caseIndex)?state.archiveRegistry.caseIndex:[]).filter(item=>(!state.selectedArchiveKey||archiveCaseKey(item)===state.selectedArchiveKey)&&(!q||`${item.institutionName||""} ${item.caseId||""} ${item.caseTitle||""} ${item.sourceSystem||""}`.toLocaleLowerCase("tr-TR").includes(q)));
  tbody.textContent="";
  for(const item of rows){const tr=document.createElement("tr"),problems=n(item.problemCount),physical=n(item.physicalReadyCount??item.presentCount),missing=n(item.missingCount),mismatch=n(item.sizeMismatchCount);tr.dataset.problem=problems>0?"true":"false";const detail=[missing?`${missing} eksik`:"",mismatch?`${mismatch} boyut uyuşmaz`:""].filter(Boolean).join(" • ");tr.innerHTML=`<td><span class="cell-title">${esc(item.institutionName||"Bilinmeyen Kurum/Daire")}</span><span class="cell-sub">${esc(item.sourceSystem==="UYAP_KURUM"?"UYAP Kurum":item.sourceSystem||"Yerel arşiv")}</span></td><td><span class="cell-title">${esc(item.caseId||"-")}</span><span class="cell-sub">${esc(item.caseTitle||"")}</span></td><td>${n(item.documentCount).toLocaleString("tr-TR")}</td><td>${physical.toLocaleString("tr-TR")}</td><td>${problems?`<span class="status-pill warn">${problems.toLocaleString("tr-TR")}</span>${detail?`<span class="cell-sub">${esc(detail)}</span>`:""}`:'<span class="status-pill good">0</span>'}</td><td>${esc(formatBytes(item.totalBytes))}</td><td>${esc(formatDate(item.latestAt))}</td>`;tbody.append(tr);}
  count.textContent=`${rows.length.toLocaleString("tr-TR")} dosya`;
  const warnings=Array.isArray(state.archiveRegistry?.registryWarnings)?state.archiveRegistry.registryWarnings:[],stale=n(state.archiveRegistry?.staleMetadataFiles);
  warning.hidden=!(warnings.length||stale);
  const notes=[];if(stale)notes.push(`${stale.toLocaleString("tr-TR")} metadata kaydının fiziksel dosyası yok veya boyutu uyuşmuyor; arşivlenmiş sayılmıyor.`);if(warnings.length)notes.push(`_archive.db kurtarmalı okundu; ${warnings.length} veritabanı uyarısı var.`);
  warning.textContent=notes.join(" ");
}
async function loadArchive(){
  const scope=$("archiveScope")?.value||"local";
  const viewRequestId=++state.archivePageRequestId;
  state.archivePageData=[];state.archivePageTotal=0;state.archivePageLoading=scope==="local";
  if(scope==="session"){
    const report=await ensureReport();
    if(!report){$("archiveRows").textContent="";$("archiveEmpty").hidden=false;return;}
    renderArchiveData();
    return;
  }
  renderArchiveRows(); // Never show a previous case while the next case summary loads.
  await refreshArchiveRegistryInBackground();
  if(viewRequestId!==state.archivePageRequestId)return;
  renderArchiveData();
  await loadArchivePage();
}
async function loadArchivePage(){
  if(($("archiveScope")?.value||"local")!=="local")return;
  const requestId=++state.archivePageRequestId;
  const selection=selectedArchiveCase();
  const query={
    sessionId:state.selectedId,
    limit:PAGE_SIZE,offset:state.archivePage*PAGE_SIZE,browseCache:true,
    search:$("archiveSearch").value.trim(),
    status:$("archiveStatus").value,
    extension:$("archiveType").value,
    institutionName:selection?.institutionName||"",
    caseId:selection?.caseId||""
  };
  state.archivePageData=[];state.archivePageTotal=0;
  state.archivePageLoading=true;state.archivePageError=null;renderArchiveRows();
  try{
    const result=await sendRuntime(MESSAGE.GET_ARCHIVE_REGISTRY,query);
    if(requestId!==state.archivePageRequestId)return;
    if(!result||!Array.isArray(result.documents)||!Number.isFinite(Number(result.matchedFiles)))
      throw new Error("ARCHIVE_REGISTRY_PAGE_INVALID");
    state.archivePageData=result.documents;
    state.archivePageTotal=n(result.matchedFiles);
    state.archiveRegistryError=null;
    if($("globalBanner")?.textContent.startsWith("Yerel arşiv"))setBanner("");
    state.archivePageError=null;
    if(!state.archiveRegistry||!state.archiveRegistry.archiveExists){
      state.archiveRegistry={...result,documents:[]};
      if(!state.selectedId){
        const available=archiveCaseEntries();
        state.selectedArchiveKey=available.some(item=>item.key===state.selectedArchiveKey)?state.selectedArchiveKey:(available[0]?.key||null);
        renderSessionPicker();renderJobs();
      }
    }
  }catch(error){
    if(requestId!==state.archivePageRequestId)return;
    state.archivePageData=[];state.archivePageTotal=0;
    state.archivePageError=String(error?.message||error);
    setBanner("Yerel arşiv sayfası okunamadı: "+state.archivePageError,"bad");
  }finally{
    if(requestId===state.archivePageRequestId){state.archivePageLoading=false;renderArchiveRows();}
  }
}
function renderArchiveRows(){
  renderArchiveCaseIndexTable();
  const scope=$("archiveScope")?.value||"local",docs=archiveRowsForScope();
  const q=$("archiveSearch").value.trim().toLocaleLowerCase("tr-TR"),status=$("archiveStatus").value,type=$("archiveType").value;
  const local=scope==="local";
  const filtered=local?docs:docs.filter(d=>{const statusMatch=!status||(status==="local_present"?archiveRowHasPhysicalFile(d):d.status===status);return statusMatch&&(!type||(d.extension||d.contentKind||"").toString().toLowerCase()===type)&&(!q||`${d.documentTitle||""} ${d.originalFilename||""} ${d.caseId||""} ${d.targetPath||""}`.toLocaleLowerCase("tr-TR").includes(q));});
  const total=local?state.archivePageTotal:filtered.length;
  const pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
  if(!local)state.archivePage=Math.min(state.archivePage,pages-1);
  const slice=local?filtered:filtered.slice(state.archivePage*PAGE_SIZE,(state.archivePage+1)*PAGE_SIZE),tbody=$("archiveRows");tbody.textContent="";
  for(const d of slice){
    const tr=document.createElement("tr"),title=d.documentTitle||d.originalFilename||"Adsız evrak",ext=(d.extension||d.contentKind||"-").toString().replace(/^\./,"").toUpperCase();
    const caseId=d.caseId||state.summary?.caseId||"-",runInfo=Number(d.runCount||0)>1?`<span class="cell-sub">${Number(d.runCount).toLocaleString("tr-TR")} çalışmada görüldü</span>`:"";
    const target=archiveRowTargetLabel(d);
    tr.innerHTML=`<td><span class="cell-title">${esc(title)}</span>${d.warnings?.length?`<span class="cell-sub">⚠ ${esc(d.warnings.join(" • "))}</span>`:""}${runInfo}</td><td>${esc(caseId)}</td><td>${esc(ext)}</td><td>${esc(archiveRowStatusLabel(d.status))}</td><td title="${esc(target)}">${esc(shortPath(target,60))}</td><td>${esc(d.documentDate||formatDate(d.updatedAt||d.completedAt))}</td>`;
    tbody.append(tr);
  }
  const readingError=local?(state.archivePageError||state.archiveRegistryError):null;
  $("archiveEmpty").hidden=!(readingError||(!state.archivePageLoading&&total===0));
  $("archiveEmpty").textContent=readingError?
    "Arşiv okunamadı: "+readingError:
    state.archivePageLoading?"Yerel arşiv yükleniyor…":"Bu filtrelerle eşleşen arşiv kaydı yok.";
  $("archivePageInfo").textContent=state.archivePageLoading?"Arşiv yükleniyor…":
    readingError?"Okuma hatası":total?`${total.toLocaleString("tr-TR")} kayıt • ${state.archivePage+1}/${pages}. sayfa`:"0 kayıt";
  $("archivePrev").disabled=state.archivePageLoading||state.archivePage<=0;$("archiveNext").disabled=state.archivePageLoading||state.archivePage>=pages-1;
  const r=state.archiveRegistry||{};
  if(scope==="local"){
    $("arPresent").textContent=n(r.presentFiles).toLocaleString("tr-TR");$("arReview").textContent="-";$("arPartial").textContent=(n(r.partialFiles)+n(r.reviewFiles)).toLocaleString("tr-TR");$("arMissing").textContent=n(r.missingFiles).toLocaleString("tr-TR");
    const pending=n(r.partialFiles)+n(r.reviewFiles);$("archiveTitle").textContent=`Fiziksel yerel arşiv • ${n(r.presentFiles).toLocaleString("tr-TR")} dosya${pending?` • ${pending.toLocaleString("tr-TR")} kontrol`:""}`;
  }else{
    const statusCount=(name)=>docs.filter(d=>String(d.status||"")===name).length;
    const downloadedRows=statusCount("verified"),localRows=statusCount("skipped"),pendingRows=statusCount("retry_wait")+statusCount("review_required")+statusCount("failed")+statusCount("partial");
    $("arPresent").textContent=downloadedRows.toLocaleString("tr-TR");$("arReview").textContent=localRows.toLocaleString("tr-TR");$("arPartial").textContent=pendingRows.toLocaleString("tr-TR");$("arMissing").textContent=statusCount("missing").toLocaleString("tr-TR");
    $("archiveTitle").textContent=state.summary?`${jobName(state.summary)} • indirilen evraklar`:"Seçili iş";
  }
}

async function loadProblems(){
  if(!state.selectedId){state.problems={review:[],deferred:[],warnings:[],reviewTotal:0,deferredTotal:0,warningTotal:0,categories:{}};renderProblems();return;}
  const [p,report]=await Promise.all([sendRuntime(MESSAGE.GET_PROBLEM_ITEMS,{sessionId:state.selectedId,limit:500}),ensureReport()]);const warnings=(report?.documents||[]).map(d=>{const terminal=["verified","skipped"].includes(String(d.status||""));const effective=(Array.isArray(d.warnings)?d.warnings:[]).filter(w=>!(terminal&&!d.errorCode&&String(w||"")==="NATIVE_AGENT_UNAVAILABLE"));return effective.length?{key:d.key,title:d.documentTitle||"Adsız evrak",status:d.status,category:"Doğrulanmış uyarı",errorCode:null,errorMessage:effective.join(" • "),attempts:n(d.attempts),targetPath:d.targetPath||null,extension:d.extension||null,sourceSystem:d.sourceSystem||"UYAP"}:null;}).filter(Boolean);state.problems={...p,warnings,warningTotal:warnings.length};renderProblems();renderProblemNavCount();
}
function renderProblemNavCount(){const count=state.summary?problems():0;const badge=$("navProblemCount");badge.textContent=String(count);badge.hidden=count<=0;}
function humanProblem(item){const code=String(item?.errorCode||"");if(code==="LOCAL_ARCHIVE_AMBIGUOUS")return"Yerel arşivde bu evrakla eşleşebilecek birden fazla doğrulanmış kayıt bulundu. Aynı evrakı ikinci kez indirmemek için indirme durduruldu; yerel arşivi kontrol edin.";if(code.includes("HTML"))return"UYAP bu evrak için HTML yanıtı döndürdü. Otomatik denemeler sonuç vermediyse bunun gerçekten HTML kaynaklı bir evrak olup olmadığını kontrol edin.";if(code.includes("TRUNCATED")||code.includes("SIZE_MISMATCH")||code.includes("NOT_COMPLETE"))return"İndirilen dosya eksik veya kesilmiş görünüyor.";if(code.includes("NATIVE")||code.includes("TRANSFER"))return"Yerel doğrulama sırasında bağlantı veya bütünlük sorunu oluştu.";if(code.includes("START")||code.includes("CORRELATED")||code.includes("TIMEOUT"))return"Evrak indirme isteği güvenli biçimde eşleştirilemedi.";return item?.errorMessage||"Bu kayıt otomatik olarak tamamlanamadı ve kontrol gerektiriyor.";}
function renderProblems(){const p=state.problems||{review:[],deferred:[],warnings:[],reviewTotal:0,deferredTotal:0,warningTotal:0,categories:{}};$("reviewBadge").textContent=String(p.reviewTotal||0);$("deferredBadge").textContent=String(p.deferredTotal||0);$("warningBadge").textContent=String(p.warningTotal||0);const categories=[...new Set([...(p.review||[]),...(p.deferred||[]),...(p.warnings||[])].map(i=>i.category).filter(Boolean))].sort(),sel=$("problemCategory"),prev=sel.value;sel.innerHTML='<option value="">Tüm kategoriler</option>'+categories.map(c=>`<option>${esc(c)}</option>`).join("");sel.value=categories.includes(prev)?prev:"";renderProblemList();const running=["running","recovering"].includes(state.summary?.status);$("repairQueueButton").disabled=!state.summary||running||state.summary?.status==="completed";}
function renderProblemList(){const p=state.problems||{},tab=state.problemTab;let rows=tab==="review"?(p.review||[]):tab==="deferred"?(p.deferred||[]):(p.warnings||[]);const q=$("problemSearch").value.trim().toLocaleLowerCase("tr-TR"),cat=$("problemCategory").value;rows=rows.filter(i=>(!cat||i.category===cat)&&(!q||`${i.title||""} ${i.errorCode||""} ${i.errorMessage||""} ${i.targetPath||""}`.toLocaleLowerCase("tr-TR").includes(q)));const list=$("problemList");list.textContent="";for(const item of rows){const row=document.createElement("label");row.className="problem-item";const selectable=tab!=="warnings";const htmlDecision=tab==="review"&&item.canConfirmHtmlExclusion===true;row.innerHTML=`${selectable?`<input type="checkbox" data-problem-key="${esc(item.key)}" ${state.selectedProblemKeys.has(item.key)?"checked":""}>`:'<span>⚠</span>'}<div><strong>${esc(item.title||"Adsız evrak")}</strong><p>${esc(tab==="warnings"?(item.errorMessage||"Kontrol önerilir"):humanProblem(item))}</p><div class="problem-tags"><span>${esc(item.category||"Diğer")}</span>${item.extension?`<span>${esc(String(item.extension).toUpperCase())}</span>`:""}<span>${n(item.attempts)} deneme</span></div>${item.errorCode?`<p class="technical-code">${esc(item.errorCode)}</p>`:""}${htmlDecision?`<button type="button" class="row-action problem-html-exclude" data-html-exclude="${esc(item.key)}">Bu gerçekten HTML geliyor • arşivden hariç tut</button>`:""}</div><span class="status-pill ${tab==="warnings"?"warn":tab==="deferred"?"warn":"bad"}">${tab==="warnings"?"Kontrol":tab==="deferred"?"Bekliyor":"İncele"}</span>`;list.append(row);}list.querySelectorAll("[data-problem-key]").forEach(cb=>cb.addEventListener("change",()=>{cb.checked?state.selectedProblemKeys.add(cb.dataset.problemKey):state.selectedProblemKeys.delete(cb.dataset.problemKey);updateProblemSelection();}));list.querySelectorAll("[data-html-exclude]").forEach(button=>button.addEventListener("click",async(event)=>{event.preventDefault();event.stopPropagation();const key=button.dataset.htmlExclude;if(!key)return;const ok=confirm("Bu evrakın UYAP tarafından gerçekten HTML olarak verildiğini doğruladınız mı? Onaylarsanız karantinadaki HTML silinir, bu kayıt yeniden denenmez ve arşivi 'yarım' bırakmaz.");if(!ok)return;button.disabled=true;try{state.summary=await sendRuntime(MESSAGE.CONFIRM_HTML_EXCLUSION,{sessionId:state.selectedId,key});state.report=null;state.problems=null;state.selectedProblemKeys.delete(key);await loadProblems();renderOverview();showToast(state.summary?.status==="completed"?"HTML kaydı hariç tutuldu; arşiv tamamlandı.":"HTML kaydı kullanıcı onayıyla hariç tutuldu ve artık yarım kayıt sayılmayacak.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}finally{button.disabled=false;}}));$("problemEmpty").hidden=rows.length>0;$("selectAllProblems").disabled=tab==="warnings"||rows.length===0;$("retrySelectedProblems").style.display=tab==="warnings"?"none":"inline-block";updateProblemSelection();}
function updateProblemSelection(){const count=state.selectedProblemKeys.size;$("selectedProblemCount").textContent=`${count} seçili`;$("retrySelectedProblems").disabled=count===0;$("retrySelectedProblems").textContent=state.problemTab==="deferred"?"Seçilenleri şimdi dene":"Seçilenleri yeniden dene";}

async function loadReports(){const report=await ensureReport();const s=report?.session||state.summary;if(!s){$("reportResult").className="report-result warn";$("reportResult").innerHTML="<h2>Raporlanacak iş yok</h2><p>Önce bir arşiv işi oluşturun.</p>";return;}state.summary=s;const integrity=s.finalIntegrity||{},ok=integrity.gatePassed===true;const allVerified=n(s.total)>0&&verified()===n(s.total)&&problems()===0&&deferred()===0;const result=$("reportResult");result.className=`report-result ${ok?"good":s.status==="incomplete"?"bad":"warn"}`;result.innerHTML=ok?(integrity.allDocumentsUserExcluded===true?`<h2>✓ Kontrol tamamlandı</h2><p>${n(integrity.userExcludedCount).toLocaleString("tr-TR")} HTML kaydı kullanıcı onayıyla hariç tutuldu. Paketlenecek evrak kalmadığı için ZIP üretilmedi; iş artık yarım sayılmıyor.</p>`:`<h2>✓ Bütünlük doğrulandı</h2><p>${n(integrity.verifiedCount).toLocaleString("tr-TR")} evrak paketlendi${n(integrity.userExcludedCount)?` • ${n(integrity.userExcludedCount).toLocaleString("tr-TR")} HTML kullanıcı onayıyla hariç tutuldu`:""}. Final paket kontrolü geçti.</p>`):s.status==="incomplete"&&allVerified?`<h2>Evraklar doğrulandı, final paket eksik</h2><p>Yeniden indirme yapmadan manifest ve ZIP üretmek için “Final paketi oluştur” düğmesini kullanın.</p>`:`<h2>Final bütünlük henüz doğrulanmadı</h2><p>İş tamamlandığında kaynak, manifest, fiziksel arşiv ve ZIP sayıları birlikte doğrulanır.</p>`;$("rpSource").textContent=n(integrity.sourceInventoryTotal||s.total).toLocaleString("tr-TR");$("rpVerified").textContent=n(integrity.verifiedCount||verified()).toLocaleString("tr-TR");$("rpManifest").textContent=n(integrity.manifestCount).toLocaleString("tr-TR");$("rpZip").textContent=`${n(integrity.verifiedPartCount)}/${n(integrity.partCount)}`;$("repackageButton").hidden=!(s.status==="incomplete"&&allVerified);$("openFinalPackageButton").hidden=!sessionHasFinalPackage(s);$("reportMeta").innerHTML=`<div><span>Kaynak</span><strong>${esc(sourceLabel(s))}</strong></div><div><span>İş</span><strong>${esc(jobName(s))}</strong></div><div><span>Başlangıç</span><strong>${esc(formatDate(s.createdAt))}</strong></div><div><span>Son güncelleme</span><strong>${esc(formatDate(s.updatedAt))}</strong></div><div><span>Arşiv durumu</span><strong>${esc(s.archiveStatus||"Bekliyor")}</strong></div><div><span>Arşiv kökü</span><strong title="${esc(s.downloadsRoot||"")}">${esc(shortPath(s.downloadsRoot||"Chrome İndirilenler/UYAP_Arsiv",55))}</strong></div>`;}

async function runHealthCheck(){
  const checks=[];try{state.agent=await sendRuntime("ping_agent");checks.push({name:"Yerel ajan bağlantısı",ok:state.agent?.ok&&state.agent?.downloadReady===true,detail:state.agent?.ok?`v${state.agent.version} • doğrulama hazır`:state.agent?.error||"Bağlantı yok"});}catch(e){state.agent={ok:false,error:String(e)};checks.push({name:"Yerel ajan bağlantısı",ok:false,detail:String(e).replace(/^Error:\s*/,"")});}
  const tabs=await getUyapTabs();checks.push({name:"UYAP/UYAP Kurum sekmesi",ok:tabs.length>0,detail:tabs.length?`${tabs.length} uygun sekme açık`:"UYAP sekmesi açık değil"});
  checks.push({name:"Uzantı sürümü",ok:true,detail:`v${VERSION}`});
  checks.push({name:"Tekrar indirme koruması",ok:true,detail:"Her evraktan hemen önce doğrulanmış yerel kopya kontrol edilir"});
  const s=state.summary;checks.push({name:"Arşiv işi",ok:Boolean(s),warn:!s,detail:s?`${sourceLabel(s)} • ${statusInfo(s).label}`:"Henüz aktif/seçili iş yok"});
  $("healthAgent").textContent=state.agent?.ok?`v${state.agent.version}`:"Bağlantı yok";$("healthAgentNote").textContent=state.agent?.downloadReady?"PDF/HTML/görüntü doğrulama hazır":state.agent?.error||"Kontrol gerekli";$("healthSource").textContent=tabs.length?"Bağlı":"Sekme yok";$("healthSourceNote").textContent=s?sourceLabel(s):"UYAP/UYAP Kurum";const archiveRoot=s?.downloadsRoot||state.archiveRegistry?.downloadsRoot||null;$("healthStorage").textContent=archiveRoot?shortPath(archiveRoot,28):"Chrome İndirilenler";$("healthStorage").title=archiveRoot||"";$("healthStorageNote").textContent="UYAP_Arsiv altında saklanır";
  const box=$("healthChecks");box.textContent="";for(const c of checks){const row=document.createElement("div");row.className="check-row";row.innerHTML=`<span class="check-dot ${c.ok?"good":c.warn?"warn":"bad"}"></span><div><strong>${esc(c.name)}</strong><span>${esc(c.detail)}</span></div>`;box.append(row);}$("sideAgent").textContent=state.agent?.ok?`Ajan v${state.agent.version} hazır`:"Ajan kontrol gerekli";renderOverviewHealth();return checks;}

function renderSupportDetails(){const s=state.summary,perf=s?.performance||{},runtime=s?.runtimeDiagnostics||{},data={extension:{version:VERSION},session:s?{sessionId:s.sessionId,sourceSystem:s.sourceSystem,status:s.status,pauseReason:s.pauseReason,total:s.total,counts:s.counts,archiveStatus:s.archiveStatus,finalIntegrity:s.finalIntegrity}:null,performance:{docsPerMinute:perf.docsPerMinute,sessionAvgDocsPerMinute:perf.sessionAvgDocsPerMinute,totalAvgMs:perf.totalAvgMs,validationAvgMs:perf.validationAvgMs,longRun:perf.longRun},runtime};$("techDetails").textContent=JSON.stringify(data,null,2);}
function downloadJsonFile(filename,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json;charset=utf-8"}));return chrome.downloads.download({url,filename,saveAs:true}).finally(()=>setTimeout(()=>URL.revokeObjectURL(url),30000));}


function initials(name,email){const src=String(name||email||"Y").trim();const parts=src.split(/\s+/).filter(Boolean);return (parts.length>1?(parts[0][0]+parts.at(-1)[0]):src.slice(0,2)).toLocaleUpperCase("tr-TR");}
async function loadAccount({force=false}={}){
  if(!globalThis.UYAPAccount)return;
  try{
    state.device=await UYAPAccount.getDeviceIdentity();
    state.account=await UYAPAccount.loadAccountSnapshot({force});
    if(state.account?.authenticated){await UYAPAccount.registerDevice({extensionVersion:VERSION,agentVersion:state.agent?.version||""}).catch(()=>null);if(force)state.account=await UYAPAccount.loadAccountSnapshot({force:true});}
  }catch(e){state.account={configured:UYAPAccount.configured(),authenticated:false,error:String(e?.message||e)};}
  renderAccount();
}
function renderAccount(){
  const a=state.account||{configured:false,authenticated:false,plan:{name:"Yerel Mod"}}, user=a.user||{}, plan=a.plan||{};
  const configured=Boolean(a.configured),signed=Boolean(a.authenticated),name=user.full_name||user.user_metadata?.full_name||user.email||"Kullanıcı",email=user.email||"";
  $("accountName").textContent=signed?name:"Yerel Mod";$("accountPlan").textContent=signed?(plan.name||"Hesap"):configured?"Giriş yapılmadı":"Hesap servisi kapalı";$("accountAvatar").textContent=initials(name,email);
  $("accountSignedOut").hidden=signed;$("accountSignedIn").hidden=!signed;$("accountConnectionBadge").textContent=signed?"Bağlı":configured?"Giriş gerekli":"Yerel mod";
  $("accountServiceMessage").textContent=configured?"Hesabınızla giriş yaparak lisans, cihaz ve AI haklarınızı etkinleştirin.":"Hesap servisi henüz yapılandırılmadı. Yerel arşivleme özellikleri çalışmaya devam eder.";
  $("signinForm").querySelectorAll("input,button").forEach(el=>el.disabled=!configured);$("signupForm").querySelectorAll("input,button").forEach(el=>el.disabled=!configured);$("forgotPasswordButton").disabled=!configured;
  if(signed){$("accountBigAvatar").textContent=initials(name,email);$("accountProfileName").textContent=name;$("accountProfileEmail").textContent=email||"-";$("accountPlanBadge").textContent=plan.name||"Plan";$("accountPlanName").textContent=plan.name||"-";$("accountSubscriptionStatus").textContent=a.subscription?.status||"-";$("accountDeviceCount").textContent=`${n(a.device_count||1)} / ${n(plan.device_limit||1)}`;$("accountArchiveAccess").textContent=a.entitlements?.archive===false?"Kapalı":"Aktif";$("accountAiAccess").textContent=a.entitlements?.ai===true?"Aktif":"Kapalı";}
  if(state.device){$("deviceName").textContent=state.device.name||"Bu bilgisayar";$("deviceId").textContent=`Cihaz: ${String(state.device.id).slice(0,12)}…`;$("deviceVersions").textContent=`Uzantı ${VERSION}${state.agent?.version?` • Ajan ${state.agent.version}`:""}`;}
  const aiReady=signed&&a.entitlements?.ai===true;document.querySelectorAll(".ai-beta").forEach(el=>el.textContent=aiReady?"AKTİF":"HAZIRLIK");
}
async function handleSignIn(ev){ev.preventDefault();try{await UYAPAccount.signIn($("signinEmail").value.trim(),$("signinPassword").value);await loadAccount({force:true});showToast("Giriş yapıldı.");}catch(e){showToast(String(e?.message||e),true);}}
async function handleSignUp(ev){ev.preventDefault();try{const r=await UYAPAccount.signUp($("signupEmail").value.trim(),$("signupPassword").value,$("signupName").value.trim());await loadAccount({force:true});showToast(r.session?"Hesap oluşturuldu ve giriş yapıldı.":"Hesap oluşturuldu. E-posta doğrulamasını tamamlayın.");}catch(e){showToast(String(e?.message||e),true);}}
async function handlePasswordReset(){const email=$("signinEmail").value.trim();if(!email){showToast("Önce e-posta adresinizi yazın.",true);return;}try{await UYAPAccount.sendPasswordReset(email);showToast("Şifre sıfırlama bağlantısı e-posta adresinize gönderildi.");}catch(e){showToast(String(e?.message||e),true);}}

function showView(view){
  const allowed=["overview","jobs","archive","timeline","problems","reports","ai","formatlab","health","support","account","settings"];state.view=allowed.includes(view)?view:"overview";
  document.querySelectorAll("[data-view-panel]").forEach(p=>p.classList.toggle("active",p.dataset.viewPanel===state.view));document.querySelectorAll(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.view===state.view));document.querySelectorAll("[data-case-view]").forEach(b=>b.classList.toggle("active",b.dataset.caseView===state.view));
  const titles={overview:"Çalışma Merkezi",jobs:"Dosyalar",archive:"Evraklar",timeline:"Zaman Çizelgesi",problems:"Sorun Merkezi",reports:"Paket & Rapor",ai:"UYAP AI",formatlab:"Format Laboratuvarı",health:"Sistem Sağlığı",support:"Destek & Tanılama",account:"Hesabım",settings:"Ayarlar"};$("pageTitle").textContent=titles[state.view];history.replaceState(null,"",`?${state.selectedId?`session=${encodeURIComponent(state.selectedId)}&`:""}view=${encodeURIComponent(state.view)}`);
  if(state.view==="archive")void loadArchive();if(state.view==="timeline")void renderTimeline();if(state.view==="ai")void renderAiWorkspace();if(state.view==="problems")void loadProblems();if(state.view==="reports")void loadReports();if(state.view==="health")void runHealthCheck();if(state.view==="support")renderSupportDetails();if(state.view==="jobs")renderJobs();
}

function onboardingDots(){return[0,1,2,3].map(i=>i===state.onboardingStep?"●":"○").join(" ");}
async function renderOnboarding(){const body=$("onboardingBody"),step=state.onboardingStep;$("onboardingDots").textContent=onboardingDots();$("onboardingBack").disabled=step===0;$("onboardingNext").textContent=step===3?"Kurulumu tamamla ✓":"Devam →";if(step===0){body.innerHTML=`<div class="onboarding-body-card"><strong>1. Uzantı hazır</strong><p>UYAP Archive v${esc(VERSION)} yüklü. Bu katman UYAP ve UYAP Kurum ekranlarını yönetir.</p><div class="onboarding-check"><span class="check-dot good"></span> Uzantı çalışıyor</div></div>`;}else if(step===1){body.innerHTML='<div class="onboarding-body-card"><strong>2. Yerel ajan kontrol ediliyor</strong><p>Dosyaların gerçek içerik imzası, SHA-256 ve final ZIP bütünlüğü yerel ajan tarafından doğrulanır.</p><div id="onAgentResult" class="onboarding-check"><span class="check-dot warn"></span> Kontrol ediliyor…</div></div>';try{const a=await sendRuntime("ping_agent");state.agent=a;$("onAgentResult").innerHTML=a?.ok&&a.downloadReady?`<span class="check-dot good"></span> Ajan v${esc(a.version)} hazır`:`<span class="check-dot bad"></span> ${esc(a?.error||"Ajan uyumlu değil")}`;}catch(e){$("onAgentResult").innerHTML=`<span class="check-dot bad"></span> ${esc(String(e).replace(/^Error:\s*/,""))}`;}}else if(step===2){const root=state.summary?.downloadsRoot||"Chrome İndirilenler klasörü / UYAP_Arsiv";body.innerHTML=`<div class="onboarding-body-card"><strong>3. Arşiv konumu</strong><p>Doğrulanmış evraklar yerel olarak UYAP_Arsiv yapısı altında tutulur.</p><div class="onboarding-check"><span class="check-dot good"></span> ${esc(shortPath(root,75))}</div></div>`;}else{const tabs=await getUyapTabs();body.innerHTML=`<div class="onboarding-body-card"><strong>4. UYAP bağlantısı</strong><p>Arşivlemeye başlamak için UYAP veya UYAP Kurum'da dava/evrak ekranını açın.</p><div class="onboarding-check"><span class="check-dot ${tabs.length?"good":"warn"}"></span> ${tabs.length?`${tabs.length} uygun UYAP sekmesi bulundu`:"UYAP sekmesi henüz açık değil. Kurulumu yine de tamamlayabilirsiniz."}</div></div>`;}}
function openOnboarding(){state.onboardingStep=0;$("onboarding").hidden=false;void renderOnboarding();}
async function closeOnboarding(mark=true){$("onboarding").hidden=true;if(mark)await chrome.storage.local.set({[ONBOARDING_KEY]:true});}

// Navigation and static actions
document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.view)));
document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.goto)));
document.querySelectorAll("[data-case-view]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.caseView)));
document.querySelectorAll("[data-ai-prompt]").forEach(b=>b.addEventListener("click",()=>setAiPrompt(b.dataset.aiPrompt,{workflowId:b.dataset.aiWorkflow||"ask"})));
$("openAiWorkspace").addEventListener("click",()=>showView("ai"));
$("timelineOpenArchive").addEventListener("click",()=>showView("archive"));$("timelineOpenAi").addEventListener("click",()=>showView("ai"));
$("timelineSearch").addEventListener("input",renderTimelineRows);$("timelineType").addEventListener("change",renderTimelineRows);
$("aiPrompt").addEventListener("input",()=>{state.aiPrompt=$("aiPrompt").value;if($("aiPromptPreview"))$("aiPromptPreview").textContent=state.aiPrompt?`Hazır komut: “${state.aiPrompt}”`:`Önerilen bir işlemi seçin veya aşağıya sorunuzu yazın.`;if($("aiSend")&&!$("aiSend").disabled)$("aiSend").textContent=isCaseSummaryPrompt(state.aiPrompt)?"Dosya özetini oluştur":"Soruyu gönder";});
$("aiFreeformMode")?.addEventListener("click",()=>{setAiPrompt("",{navigate:false,workflowId:"ask"});$("aiPrompt")?.focus();});
$("aiArchiveCasePicker")?.addEventListener("change",async()=>{state.aiSelectedCaseKey=$("aiArchiveCasePicker").value||null;state.aiIndex=null;state.aiCurrentResult=null;state.aiSummaries=[];state.aiUndo=null;state.aiDeleteConfirm=null;if($("aiSearchResults"))$("aiSearchResults").textContent="";if($("aiAnswerBox")){$("aiAnswerBox").hidden=true;$("aiAnswerBox").textContent="";}if($("aiResultPanel"))$("aiResultPanel").hidden=true;renderAiSummaryHistory();await renderAiWorkspace({reuseCases:true});});
$("aiSummaryPdf")?.addEventListener("click",async()=>{if(!state.aiCurrentResult)return showToast("PDF için önce bir sonuç oluşturun veya geçmişten açın.",true);try{await openSummaryPrint(state.aiCurrentResult);}catch(error){showToast(String(error).replace(/^Error:\s*/,""),true);}});
$("aiSavedFilters")?.addEventListener("click",event=>{
  const button=event.target.closest("[data-ai-saved-filter]");if(!button)return;
  state.aiSavedFilter=button.dataset.aiSavedFilter;state.aiDeleteConfirm=null;renderAiSummaryHistory();
});
$("aiSummaryHistory")?.addEventListener("click",async(event)=>{
  const button=event.target.closest("button");if(!button)return;
  const selected=selectedAiArchiveCase();if(!selected)return;
  const data=button.dataset,find=id=>state.aiSummaries.find(item=>item.summaryId===id);
  const message=error=>String(error).replace(/^Error:\s*/,"");
  if(data.aiSavedOpen||data.aiSavedPdf){
    const item=find(data.aiSavedOpen||data.aiSavedPdf);if(!item)return;
    try{
      const result=savedToResult(await loadSavedResult(item,selected),selected);
      if(data.aiSavedOpen)renderAiResult(result,selected,{fromHistory:true});
      else await openSummaryPrint(result);
    }catch(error){showToast(message(error),true);}
    return;
  }
  if(data.aiSavedDelete||data.aiSavedDeleteCancel){
    state.aiDeleteConfirm=data.aiSavedDelete||null;renderAiSummaryHistory();return;
  }
  if(data.aiSavedDeleteConfirm){
    const item=find(data.aiSavedDeleteConfirm);state.aiDeleteConfirm=null;
    if(!item){renderAiSummaryHistory();return;}
    button.disabled=true;
    try{
      await sendRuntime(MESSAGE.DELETE_AI_ARCHIVE_SUMMARY,{institutionName:selected.institutionName,caseId:selected.caseId,summaryId:item.summaryId});
      state.aiSummaries=state.aiSummaries.filter(row=>row.summaryId!==item.summaryId);
      state.aiUndo={summaryId:item.summaryId,label:`${item.workflowTitle||AI_WORKFLOW_TITLES[savedWorkflow(item)]||"Kayıt"} · ${formatDate(item.createdAt)}`,until:Date.now()+AI_UNDO_MS};
      if(state.aiCurrentResult?.summaryId===item.summaryId){
        state.aiCurrentResult={...state.aiCurrentResult,summaryId:null,savedSummary:null};
        const saved=$("aiSummarySavedBadge");if(saved)saved.hidden=true;
      }
      renderAiSummaryHistory();
      setTimeout(()=>{if(state.aiUndo?.summaryId===item.summaryId){state.aiUndo=null;renderAiSummaryHistory();}},AI_UNDO_MS);
    }catch(error){renderAiSummaryHistory();showToast(message(error),true);}
    return;
  }
  if(data.aiSavedUndo){
    button.disabled=true;
    try{
      await sendRuntime(MESSAGE.RESTORE_AI_ARCHIVE_SUMMARY,{institutionName:selected.institutionName,caseId:selected.caseId,summaryId:data.aiSavedUndo});
      state.aiUndo=null;showToast("Kayıt geri alındı.");
      await loadAiSummaryHistory(selected);
    }catch(error){state.aiUndo=null;renderAiSummaryHistory();showToast(message(error),true);}
  }
});
$("aiBuildIndex").addEventListener("click",async()=>{
  const selected=selectedAiArchiveCase();
  if(!selected)return showToast("Önce yerel arşivden bir dava dosyası seçin.",true);
  if(selected.readyForAi!==true)return showToast("Bu dava dosyasının tüm evrakları fiziksel olarak hazır değil.",true);
  const button=$("aiBuildIndex"),label=$("aiIndexStatus");
  button.disabled=true;button.textContent="Tam dava indeksi hazırlanıyor…";
  if(label)label.textContent=`${n(selected.totalDocuments).toLocaleString("tr-TR")} fiziksel evrak SHA-256/metadata doğrulamasından geçirilip yerelde indeksleniyor.`;
  try{
    const result=await sendRuntime(MESSAGE.BUILD_AI_ARCHIVE_CASE_INDEX,{institutionName:selected.institutionName,caseId:selected.caseId});
    state.aiIndex=result;
    showToast(`Tam dava AI indeksi hazır: ${n(result.availableDocuments)} metin evrakı, ${n(result.availableChunks)} parça, ${n(result.unsupportedDocuments)} metin çıkarılamayan${n(result.qualityRejectedDocuments)?`, ${n(result.qualityRejectedDocuments)} PDF kalite kapısında dışlandı`:""}.`);
    await renderAiWorkspace();
  }catch(error){
    if(label)label.textContent=String(error).replace(/^Error:\s*/,"");
    showToast(String(error).replace(/^Error:\s*/,""),true);
  }finally{button.disabled=false;}
});
$("aiRetrieve").addEventListener("click",async()=>{
  const selected=selectedAiArchiveCase();
  if(!selected)return showToast("Önce yerel arşivden bir dava dosyası seçin.",true);
  const query=String($("aiPrompt")?.value||"").trim();
  if(!query)return showToast("Önce aramak istediğiniz soruyu veya anahtar kelimeyi yazın.",true);
  const button=$("aiRetrieve"),box=$("aiSearchResults");
  button.disabled=true;button.textContent="Tam dava arşivinde aranıyor…";
  if(box)box.innerHTML='<div class="ai-search-empty">Seçilen dava dosyasının yerel metin parçaları taranıyor…</div>';
  try{
    const result=await sendRuntime(MESSAGE.SEARCH_AI_ARCHIVE_CASE,{institutionName:selected.institutionName,caseId:selected.caseId,query,limit:8});
    const rows=Array.isArray(result?.results)?result.results:[];
    if(!box)return;
    if(!rows.length){
      box.innerHTML='<div class="ai-search-empty">Bu sorgu için seçilen dava arşivinde eşleşen kaynak bulunamadı.</div>';
      return;
    }
    box.innerHTML=rows.map(row=>`<article class="ai-search-result"><div><strong>${esc(row.sourceRef||"-")}</strong><span>${esc(row.documentType||"DOSYA")}${row.documentDate?` • ${esc(shortDate(row.documentDate))}`:""}</span></div><h4>${esc(row.title||"Adsız evrak")}</h4><p>${esc(row.snippet||"")}</p></article>`).join("");
  }catch(error){
    if(box)box.innerHTML=`<div class="ai-search-empty bad-text">${esc(String(error).replace(/^Error:\s*/,""))}</div>`;
    showToast(String(error).replace(/^Error:\s*/,""),true);
  }finally{
    button.disabled=!(state.aiIndex?.ready===true);button.textContent="Yerel dava kaynaklarında ara";
  }
});
$("aiSend").addEventListener("click",async()=>{
  const selected=selectedAiArchiveCase();
  if(!selected)return showToast("Önce yerel arşivden bir dava dosyası seçin.",true);
  if(selected.readyForAi!==true)return showToast("AI kullanılamıyor: dava arşivinde eksik veya kontrol bekleyen evraklar var.",true);
  const query=String($("aiPrompt")?.value||"").trim();
  if(!query)return showToast("Önce AI’ya sorulacak metni yazın.",true);
  const summaryMode=isCaseSummaryPrompt(query),button=$("aiSend");
  button.disabled=true;button.textContent=summaryMode?"Dosya özeti hazırlanıyor…":(AI_RULE_WORKFLOWS[state.aiWorkflowId]?"Kayıtlar okunuyor…":"Kaynaklı yanıt hazırlanıyor…");
  renderAiResultLoading(summaryMode);
  try{
    const result=await sendRuntime(MESSAGE.AI_ASK_ARCHIVE_CASE,{institutionName:selected.institutionName,caseId:selected.caseId,workflowId:state.aiWorkflowId,query,limit:summaryMode?6:8});
    const displayResult={
      ...result,
      createdAt:result?.savedSummary?.createdAt||new Date().toISOString(),
      summaryId:result?.savedSummary?.summaryId||null,
      institutionName:selected.institutionName,
      caseId:selected.caseId,
      archiveCase:selected
    };
    renderAiResult(displayResult,selected);
    const ruleResult=Boolean(ruleSectionsOf(result));
    if(summaryMode||ruleResult){
      if(result?.summarySaveError)showToast((summaryMode?"Özet":"Sonuç")+" oluşturuldu ancak yerel arşive kaydedilemedi: "+result.summarySaveError,true);
      else if(summaryMode)showToast(result?.savedSummary?.summaryId?"Dosya özeti yerel arşive kaydedildi.":"Dosya özeti oluşturuldu.");
      await loadAiSummaryHistory(selected);
    }
  }catch(error){
    renderAiResultError(error);
    showToast(String(error).replace(/^Error:\s*/,""),true);
  }finally{
    state.aiSendGate={caseSelected:true,caseReady:selected.readyForAi===true,providerReady:state.aiService?.enabled===true&&state.aiService?.configured===true,indexReady:state.aiIndex?.ready===true&&selected.readyForAi===true};
    updateAiSendButton();
  }
});
$("globalSearch").addEventListener("keydown",event=>{if(event.key!=="Enter")return;const q=$("globalSearch").value.trim();$("jobSearch").value=q;showView("jobs");renderJobs();});
$("sessionPicker").addEventListener("change",async()=>{const value=$("sessionPicker").value||"";if(value.startsWith("archive::")){state.selectedId=null;state.selectedArchiveKey=decodeURIComponent(value.slice(9));}else{state.selectedId=value||null;state.selectedArchiveKey=null;}state.archivePage=0;++state.archivePageRequestId;state.archivePageData=[];state.archivePageTotal=0;await loadSelectedSummary();showView(state.view);});
$("refreshButton").addEventListener("click",async()=>{await loadSessions({preserve:true});state.report=null;state.problems=null;workspaceReportDirty=true;workspaceRegistryDirty=true;lastWorkspaceReportRefreshAt=0;lastWorkspaceRegistryRefreshAt=0;await refreshWorkspaceLiveState();if(state.view==="health")await runHealthCheck();showToast("Veriler yenilendi.");});
$("jobSearch").addEventListener("input",renderJobs);$("jobStatusFilter").addEventListener("change",renderJobs);$("jobGrouping").addEventListener("change",()=>{renderJobs();renderRecentJobs();});
$("archiveScope").addEventListener("change",()=>{state.archivePage=0;$("archiveStatus").value=$("archiveScope").value==="session"?"verified":"local_present";if($("archiveScope").value==="local")workspaceRegistryDirty=true;else workspaceReportDirty=true;void loadArchive();});
let archiveSearchDelay=null;
$("archiveSearch").addEventListener("input",()=>{
  state.archivePage=0;
  if($("archiveScope").value!=="local"){renderArchiveRows();return;}
  ++state.archivePageRequestId; // Discard pages fetched for the previous search.
  state.archivePageData=[];state.archivePageTotal=0;state.archivePageLoading=true;
  renderArchiveRows();
  clearTimeout(archiveSearchDelay);
  archiveSearchDelay=setTimeout(()=>void loadArchivePage(),350);
});
for(const id of ["archiveStatus","archiveType"]){$(id).addEventListener("change",()=>{state.archivePage=0;if($("archiveScope").value==="local")void loadArchivePage();else renderArchiveRows();});}
$("archivePrev").addEventListener("click",()=>{state.archivePage=Math.max(0,state.archivePage-1);if($("archiveScope").value==="local")void loadArchivePage();else renderArchiveRows();});
$("archiveNext").addEventListener("click",()=>{state.archivePage+=1;if($("archiveScope").value==="local")void loadArchivePage();else renderArchiveRows();});
$("problemTabs").querySelectorAll("[data-problem-tab]").forEach(b=>b.addEventListener("click",()=>{state.problemTab=b.dataset.problemTab;state.selectedProblemKeys.clear();$("problemTabs").querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));renderProblemList();}));$("problemSearch").addEventListener("input",renderProblemList);$("problemCategory").addEventListener("change",renderProblemList);$("selectAllProblems").addEventListener("click",()=>{const p=state.problems||{};let rows=state.problemTab==="review"?(p.review||[]):state.problemTab==="deferred"?(p.deferred||[]):[];const q=$("problemSearch").value.trim().toLocaleLowerCase("tr-TR"),cat=$("problemCategory").value;rows=rows.filter(i=>(!cat||i.category===cat)&&(!q||`${i.title||""} ${i.errorCode||""}`.toLocaleLowerCase("tr-TR").includes(q)));rows.forEach(i=>state.selectedProblemKeys.add(i.key));renderProblemList();});
$("retrySelectedProblems").addEventListener("click",async()=>{if(!state.selectedId||!state.selectedProblemKeys.size)return;const button=$("retrySelectedProblems");button.disabled=true;button.textContent="Yeniden başlatılıyor…";try{const result=await sendRuntime(MESSAGE.RETRY_SELECTED_PROBLEMS,{sessionId:state.selectedId,keys:[...state.selectedProblemKeys],queue:state.problemTab==="deferred"?"deferred":"review"});state.summary=result.session||state.summary;const resumed=result.autoResumed===true;const msg=state.problemTab==="deferred"?`${n(result.expedited)} kayıt öne alındı${resumed?" ve iş yeniden başlatıldı":""}.`:(n(result.revalidated)>0?`${n(result.revalidated)} kayıt yerel dosyadan yeniden doğrulandı; ${n(result.queued)} kayıt yeniden indirme kuyruğuna alındı${resumed?" ve iş yeniden başlatıldı":""}.`:`${n(result.queued)} kayıt yeniden deneme kuyruğuna alındı${resumed?" ve iş yeniden başlatıldı":""}.`);showToast(msg);state.selectedProblemKeys.clear();state.problems=null;await loadProblems();renderOverview();}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}finally{updateProblemSelection();}});
$("repairQueueButton").addEventListener("click",async()=>{if(!state.selectedId)return;const ok=confirm("Yalnız tamamlanmamış çalışma durumları yeniden kuyruğa alınacak. Doğrulanmış dosyalar ve fiziksel arşiv korunacak. Devam edilsin mi?");if(!ok)return;try{state.summary=await sendRuntime(MESSAGE.RESET_RUNTIME,{sessionId:state.selectedId});showToast(`${n(state.summary.lastRuntimeResetCount)} tamamlanmamış kayıt yeniden kuyruğa alındı.`);state.problems=null;await loadProblems();renderOverview();}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}});
$("openReportButton").addEventListener("click",()=>void openReport());$("repackageButton").addEventListener("click",async()=>{if(!state.selectedId)return;try{showToast("Final ZIP ve manifest oluşturuluyor…");state.summary=await sendRuntime(MESSAGE.REPACKAGE_ARCHIVE,{sessionId:state.selectedId});state.report=null;await loadReports();renderOverview();showToast(state.summary?.archiveStatus==="VERIFIED_COMPLETE"?"Final paket oluşturuldu ve bütünlük doğrulandı.":"Paketleme tamamlandı; rapor sonucunu kontrol edin.",state.summary?.archiveStatus!=="VERIFIED_COMPLETE");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}});
async function openArchiveFolder(){try{const result=await sendRuntime(MESSAGE.OPEN_ARCHIVE_FOLDER,{sessionId:state.selectedId});showToast(result?.path?`Arşiv klasörü açıldı: ${shortPath(result.path,50)}`:"Arşiv klasörü açıldı.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}}async function openFinalPackage(){if(!state.selectedId)return showToast("Önce bir arşiv işi seçin.",true);try{const result=await sendRuntime(MESSAGE.OPEN_FINAL_PACKAGE_FOLDER,{sessionId:state.selectedId});showToast(result?.path?`Final paket açıldı: ${shortPath(result.path,55)}`:"Final paket klasörü açıldı.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}}
async function createNewJob(){const ok=confirm("Mevcut işler ve indirilen dosyalar korunacak. Açık UYAP ekranından yeni arşiv işi oluşturulsun mu?");if(!ok)return;try{const tab=await activeUyapTab(),s=await sendRuntime(MESSAGE.NEW_JOB,{tabId:tab.id});await loadSessions({preserve:false});state.selectedId=s.sessionId;renderSessionPicker();await loadSelectedSummary();showView("overview");showToast("Yeni arşiv işi oluşturuldu.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}}
async function runNextAction(){const action=$("nextActionButton")?.dataset.action||"new";if(action==="new")return createNewJob();if(action==="package")return openFinalPackage();if(action==="repackage"){showView("reports");$("repackageButton")?.focus();return;}showView(action);}

$("openArchiveFolder").addEventListener("click",()=>void openArchiveFolder());$("openStorageFromHealth").addEventListener("click",()=>void openArchiveFolder());$("quickOpenArchive").addEventListener("click",()=>showView("archive"));$("quickOpenPackage").addEventListener("click",()=>void openFinalPackage());$("openFinalPackageButton").addEventListener("click",()=>void openFinalPackage());$("nextActionButton").addEventListener("click",()=>void runNextAction());$("headerNewJob").addEventListener("click",()=>void createNewJob());
$("runHealthCheck").addEventListener("click",()=>void runHealthCheck().then(()=>showToast("Sistem kontrolleri yenilendi.")));$("testNotification").addEventListener("click",async()=>{try{await sendRuntime(MESSAGE.TEST_NOTIFICATION);showToast("Test bildirimi gönderildi.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}});
$("downloadDiagnostic").addEventListener("click",async()=>{if(!state.selectedId)return showToast("Önce bir arşiv işi seçin.",true);try{const data=await sendRuntime(MESSAGE.GET_DIAGNOSTIC_DATA,{sessionId:state.selectedId});const stamp=new Date().toISOString().replaceAll(":","-").replace(/\.\d{3}Z$/,"Z");await downloadJsonFile(`UYAP_Destek_Paketi_${stamp}.json`,data);showToast("Privacy-light destek paketi oluşturuldu.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}});
$("downloadSourceDiagnostic").addEventListener("click",async()=>{try{const tab=await activeUyapTab(),data=await sendRuntime(MESSAGE.SOURCE_DIAGNOSTIC,{tabId:tab.id}),stamp=new Date().toISOString().replaceAll(":","-").replace(/\.\d{3}Z$/,"Z");await downloadJsonFile(`UYAP_Ekran_Tanisi_${stamp}.json`,data);showToast("UYAP ekran tanısı oluşturuldu.");}catch(e){showToast(String(e).replace(/^Error:\s*/,""),true);}});$("copyTechDetails").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("techDetails").textContent||"");showToast("Teknik ayrıntılar panoya kopyalandı.");}catch(e){showToast("Panoya kopyalanamadı.",true);}});
$("newJobFromWorkspace").addEventListener("click",()=>void createNewJob());

for(const [id,key] of [["settingLoginNotification","loginNotification"],["settingCompletionNotification","completionNotification"],["settingProblemNotification","problemNotification"],["settingTechnical","showTechnical"]]){$(id).addEventListener("change",()=>{state.settings[key]=$(id).checked;void saveSettings();});}
$("rerunOnboarding").addEventListener("click",openOnboarding);$("resetUiSettings").addEventListener("click",async()=>{state.settings={...DEFAULT_SETTINGS};await chrome.storage.local.set({[SETTINGS_KEY]:state.settings});applySettingsForm();showToast("Arayüz ayarları sıfırlandı.");});
$("onboardingBack").addEventListener("click",()=>{state.onboardingStep=Math.max(0,state.onboardingStep-1);void renderOnboarding();});$("onboardingNext").addEventListener("click",async()=>{if(state.onboardingStep>=3){await closeOnboarding(true);showToast("Kurulum tamamlandı. UYAP Archive kullanıma hazır.");return;}state.onboardingStep+=1;await renderOnboarding();});$("skipOnboarding").addEventListener("click",()=>void closeOnboarding(true));


$("accountMenuButton").addEventListener("click",()=>showView("account"));
document.querySelectorAll("[data-auth-tab]").forEach(btn=>btn.addEventListener("click",()=>{document.querySelectorAll("[data-auth-tab]").forEach(x=>x.classList.toggle("active",x===btn));$("signinForm").hidden=btn.dataset.authTab!=="signin";$("signupForm").hidden=btn.dataset.authTab!=="signup";}));
$("signinForm").addEventListener("submit",ev=>void handleSignIn(ev));
$("signupForm").addEventListener("submit",ev=>void handleSignUp(ev));
$("forgotPasswordButton").addEventListener("click",()=>void handlePasswordReset());
$("refreshAccountButton").addEventListener("click",()=>void loadAccount({force:true}));
$("signOutButton").addEventListener("click",async()=>{await UYAPAccount.signOut();await loadAccount({force:true});showToast("Hesaptan çıkış yapıldı. Yerel arşiviniz korunuyor.");});

async function init(){
  $("sideVersion").textContent=`v${VERSION}`;$("healthExtension").textContent=`v${VERSION}`;$("aboutVersion").textContent=VERSION;
  const onboardingDone=await loadSettings();await loadSessions({preserve:false});await loadAccount();void runHealthCheck().then(()=>renderAccount());
  const requestedView=new URLSearchParams(location.search).get("view")||"overview";showView(requestedView);
  if(!onboardingDone)openOnboarding();
  document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")void refreshWorkspaceLiveState();});
  setInterval(()=>void refreshWorkspaceLiveState(),WORKSPACE_LIVE_REFRESH_MS);
}
void init();
