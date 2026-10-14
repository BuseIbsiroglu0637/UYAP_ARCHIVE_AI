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
async function main(){
  const data=await chrome.storage.local.get(KEY),payload=data[KEY];
  if(!payload){document.getElementById("summary").innerHTML="<p>Yazdırılacak özet bulunamadı.</p>";return;}
  document.getElementById("caseTitle").textContent=[payload.institutionName,payload.caseId].filter(Boolean).join(" • ")||"Dava dosyası";
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
