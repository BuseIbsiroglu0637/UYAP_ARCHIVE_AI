(()=>{
  const $=id=>document.getElementById(id);
  let lastReport=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function send(type,payload={}){const r=await chrome.runtime.sendMessage({type,...payload});if(!r?.ok)throw new Error(r?.error||'İşlem başarısız.');return r.data;}
  async function activeUyapTab(){const tabs=await chrome.tabs.query({url:['https://*.uyap.gov.tr/*','https://uyap.gov.tr/*']});const t=tabs.find(x=>x.active)||tabs[0];if(!t?.id)throw new Error('Açık UYAP/UYAP Kurum sekmesi bulunamadı.');return t;}
  function rows(obj){const entries=Object.entries(obj||{}).sort((a,b)=>Number(b[1])-Number(a[1]));if(!entries.length)return '<div class="empty-state">Kayıt yok.</div>';return entries.map(([k,v])=>`<div class="format-row"><span>${esc(k)}</span><strong>${Number(v).toLocaleString('tr-TR')}</strong></div>`).join('');}
  function riskRow(label,ok,detail){return `<div class="check-row"><span class="check-dot ${ok?'good':'bad'}"></span><div><strong>${esc(label)}</strong><span>${esc(detail)}</span></div></div>`;}
  function render(report){
    lastReport=report; $('downloadFormatLab').disabled=false;
    const census=report?.census?.primaryFrame||null;
    const audit=report?.audit?.ok?report.audit:null;
    $('flSourceTotal').textContent=Number(census?.totalNodes||0).toLocaleString('tr-TR');
    $('flLocalTotal').textContent=Number(audit?.scannedLocalFiles||0).toLocaleString('tr-TR');
    $('flUnknown').textContent=Number(audit?.unknownCount||0).toLocaleString('tr-TR');
    $('flInvalid').textContent=Number(audit?.invalidCount||0).toLocaleString('tr-TR');
    $('formatLabFormats').innerHTML=rows(audit?.formatCounts);
    $('formatLabSource').innerHTML=rows(census?.typeCounts||census?.uyapTypeCounts);
    const risks=[];
    risks.push(riskRow('Bilinmeyen byte yapısı',Number(audit?.unknownCount||0)===0,audit?`${audit.unknownCount||0} bilinmeyen dosya`:'Yerel audit alınamadı'));
    risks.push(riskRow('Validator regresyonu',Number(audit?.invalidCount||0)===0,audit?`${audit.invalidCount||0} yapısal uyumsuzluk`:'Yerel audit alınamadı'));
    risks.push(riskRow('Aktif dava kapsamı',Boolean(census?.caseConfirmed),census?.caseConfirmed?`${census.totalNodes||0} evrak tek dava dalında`:'Aktif dava dalı kesinleştirilemedi'));
    risks.push(riskRow('Metadata AJAX ayrımı',Boolean(report?.requestResolver?.metadataAjaxIgnored),'*.ajx çağrıları gerçek evrak fallback adayı yapılmaz'));
    risks.push(riskRow('Viewer önceliği',Boolean(report?.requestResolver?.viewerMimePreferred),'viewer.jsp + mimeType gerçek evrak adayı olarak önceliklendirilir'));
    $('formatLabRisks').innerHTML=risks.join('');
    $('formatLabResolver').innerHTML=`<div><span>Metadata AJAX</span><strong>${report?.requestResolver?.metadataAjaxIgnored?'Hariç tutuluyor':'Kontrol gerekli'}</strong></div><div><span>Viewer mimeType</span><strong>${report?.requestResolver?.viewerMimePreferred?'Öncelikli':'Kontrol gerekli'}</strong></div><div><span>İlk request kör fallback</span><strong>${report?.requestResolver?.firstRequestBlindFallback?'Açık':'Kapalı'}</strong></div><div><span>Ajan</span><strong>${esc(audit?.agentVersion||'-')}</strong></div>`;
    const issues=Number(audit?.unknownCount||0)+Number(audit?.invalidCount||0);
    $('formatLabStatus').className=`format-lab-status ${issues?'warn':'good'}`;
    $('formatLabStatus').textContent=issues?`Analiz tamamlandı: ${issues} yeni/şüpheli yapı incelenmeli.`:`Analiz tamamlandı: bilinen yerel yapılarda format açığı görülmedi.`;
  }
  async function run(){
    const sessionId=$('sessionPicker')?.value||''; if(!sessionId){$('formatLabStatus').textContent='Önce bir arşiv işi seçin.';return;}
    $('runFormatLab').disabled=true;$('formatLabStatus').className='format-lab-status';$('formatLabStatus').textContent='UYAP kaynak ağacı ve yerel dosyalar analiz ediliyor…';
    try{const tab=await activeUyapTab();render(await send('format_lab_report',{sessionId,tabId:tab.id}));}
    catch(e){$('formatLabStatus').className='format-lab-status bad';$('formatLabStatus').textContent=String(e).replace(/^Error:\s*/,'');}
    finally{$('runFormatLab').disabled=false;}
  }
  function download(){if(!lastReport)return;const blob=new Blob([JSON.stringify(lastReport,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`UYAP_Format_Laboratuvari_${new Date().toISOString().replaceAll(':','-').replace(/\.\d{3}Z$/,'Z')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  $('runFormatLab')?.addEventListener('click',run);$('downloadFormatLab')?.addEventListener('click',download);
  document.querySelector('[data-view="formatlab"]')?.addEventListener('click',()=>setTimeout(run,0));
  $('sessionPicker')?.addEventListener('change',()=>{if(document.querySelector('[data-view-panel="formatlab"]')?.classList.contains('active'))setTimeout(run,0);});
})();
