const KEY="uyapAiPrintSummaryV1";
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
function formatDate(value){
  const date=new Date(value||0);
  return Number.isNaN(date.getTime())?"-":new Intl.DateTimeFormat("tr-TR",{dateStyle:"medium",timeStyle:"short"}).format(date);
}
function normalizeHeading(value){
  return String(value||"").replace(/^#{1,6}\s*/,"").replace(/^\*\*(.*?)\*\*:?$/,"$1").replace(/^__(.*?)__:?$/,"$1").replace(/:$/,"").trim().toLocaleLowerCase("tr-TR");
}
// case_summary_v2 headings first, then the four legacy v1 headings.
const SECTIONS=[
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
const withRefs=value=>esc(value).replace(/\[(D\d{6})\]/g,'<span class="ref">$1</span>');
function parseSummary(answer){
  const aliases=new Map(SECTIONS.flatMap(item=>item.aliases.map(alias=>[alias,item.key])));
  const sections=Object.fromEntries([...SECTIONS.map(item=>[item.key,[]]),["other",[]]]);
  let current="other";
  for(const raw of String(answer||"").split(/\r?\n/)){
    const line=raw.trim();if(!line)continue;
    const heading=normalizeHeading(line);
    if(aliases.has(heading)){current=aliases.get(heading);continue;}
    const cleaned=line.replace(/^[-•*]\s*/,"").replace(/^\d+[.)]\s*/,"").replace(/^\*\*(.*?)\*\*$/,"$1").trim();
    if(cleaned)sections[current].push(cleaned);
  }
  return sections;
}
function section(title,items,list=false,highlight=false){
  if(!items?.length)return "";
  const body=list
    ?`<ul>${items.slice(0,16).map(item=>`<li>${withRefs(item)}</li>`).join("")}</ul>`
    :items.slice(0,4).map(item=>`<p>${withRefs(item)}</p>`).join("");
  return `<section${highlight?' class="is-key"':""}><h2>${esc(title)}</h2>${body}</section>`;
}
// Rule views (Önemli Tarihler, Ara Kararlar ve Yapılacaklar): the same sections
// the workspace shows, laid out for paper.
const TONES=new Set(["good","warn","bad","info","muted"]);
const trDate=value=>{const match=String(value||"").match(/^(\d{4})-(\d{2})-(\d{2})/);return match?`${match[3]}.${match[2]}.${match[1]}`:String(value||"");};
const tone=value=>TONES.has(value)?value:"muted";
function refsHtml(refs,more){
  return (Array.isArray(refs)?refs:[]).map(ref=>`<span class="ref">${esc(ref)}</span>`).join("")+(more?`<span class="ref">+${esc(more)}</span>`:"");
}
function ruleSectionHtml(section){
  const items=Array.isArray(section?.items)?section.items:[];
  const rows=items.map(item=>{
    const status=item?.status?.label?`<span class="badge tone-${tone(item.status.tone)}">${esc(item.status.label)}</span>`:"";
    const who=item?.who?`<b>${esc(item.who)}</b> — `:"";
    const main=item?.kind==="party"?`<b>${esc(item.text||"")}</b>`:esc(item?.text||"");
    const isNew=item?.isNew?'<span class="badge tone-new">YENİ</span>':"";
    const lines=(Array.isArray(item?.lines)?item.lines:[]).map(line=>`<li class="tone-${tone(line.tone)}">${esc(line.text||"")}${refsHtml(line.refs,line.moreRefs)}</li>`).join("");
    const note=item?.kind==="note"||!item?.dateText;
    return `<div class="row${note?" note":""}">${note?"":`<div class="when">${esc(item.dateText)}</div>`}<div class="what">${isNew}${who}${main}${status}${refsHtml(item?.refs,item?.moreRefs)}${lines?`<ul class="sub">${lines}</ul>`:""}</div></div>`;
  }).join("")||`<p class="empty">${esc(section?.empty||"Kayıt yok.")}</p>`;
  const subtitle=section?.subtitle?` <small>${esc(section.subtitle)}</small>`:"";
  return `<section class="rule${section?.highlight?" is-key":""}"><h2>${esc(section?.title||"")}${subtitle}${refsHtml(section?.refs)}</h2>${rows}</section>`;
}
function renderRuleDocument(payload){
  document.getElementById("summary").innerHTML=payload.sections.map(ruleSectionHtml).join("");
  if(payload.workflowNotice){const notice=document.getElementById("notice");notice.textContent=payload.workflowNotice;notice.hidden=false;}
  document.getElementById("footerNote").textContent="Bu belge yerel arşivdeki evraklardan kurallarla çıkarılmıştır; AI modeli kullanılmamıştır ve hukuki görüş yerine geçmez. Süreleri karar metni ve tebliğ mazbatasından; tatil ve adli tatile göre kontrol edin.";
  // On paper a code like D000075 says nothing; list which document each one is.
  const refs=new Set(Array.isArray(payload.sourceRefs)?payload.sourceRefs:[]);
  const sources=(Array.isArray(payload.sources)?payload.sources:[]).filter(source=>!refs.size||refs.has(source.sourceRef));
  document.getElementById("sources").innerHTML=sources.length
    ?'<h2>Kaynak evraklar</h2>'+sources.map(source=>`<div class="source"><span>${esc(source.sourceRef||"-")}</span><div><strong>${esc(source.title||"Adsız evrak")}</strong>${source.documentDate?`<small>${esc(trDate(source.documentDate))}</small>`:""}</div></div>`).join("")
    :"";
}
async function main(){
  const data=await chrome.storage.local.get(KEY),payload=data[KEY];
  if(!payload){document.getElementById("summary").innerHTML="<p>Yazdırılacak özet bulunamadı.</p>";return;}
  const docTitle=payload.documentTitle||"Dosya Özeti";
  document.title=payload.title||("UYAP Archive AI - "+docTitle);
  document.getElementById("docTitle").textContent=docTitle;
  document.getElementById("caseTitle").textContent=[payload.institutionName,payload.caseId].filter(Boolean).join(" • ")||"Dava dosyası";
  if(Array.isArray(payload.sections)&&payload.sections.length){
    document.getElementById("meta").innerHTML=[
      ["Oluşturulma",formatDate(payload.createdAt)],
      ["Yöntem",payload.provider||"Yerel kurallar"],
      ["Kaynak evrak",String((payload.sourceRefs||[]).length)]
    ].map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    renderRuleDocument(payload);
    return;
  }
  document.getElementById("meta").innerHTML=[
    ["Oluşturulma",formatDate(payload.createdAt)],
    ["Model",payload.model||"-"],
    ["Kaynak sayısı",String((payload.sources||[]).length)]
  ].map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  const parsed=parseSummary(payload.answer);
  document.getElementById("summary").innerHTML=
    SECTIONS.map(item=>section(item.title,parsed[item.key],item.list,item.highlight)).join("")||
    `<section><p>${esc(payload.answer||"Özet bulunamadı.")}</p></section>`;
  const sources=Array.isArray(payload.sources)?payload.sources:[];
  document.getElementById("sources").innerHTML=sources.length
    ?'<h2>Özet için kullanılan evraklar</h2>'+sources.map(source=>`<div class="source"><span>${esc(source.sourceRef||"-")}</span><div><strong>${esc(source.title||"Adsız evrak")}</strong><small>${esc(source.selectionReason||"Doğrulanmış kaynak")}${source.documentDate?" • "+esc(source.documentDate):""}</small></div></div>`).join("")
    :"";
}
document.getElementById("printButton").addEventListener("click",()=>window.print());
document.getElementById("closeButton").addEventListener("click",()=>window.close());
void main().then(()=>setTimeout(()=>window.print(),350));
