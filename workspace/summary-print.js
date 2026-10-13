const KEY="uyapAiPrintSummaryV1";
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
function formatDate(value){
  const date=new Date(value||0);
  return Number.isNaN(date.getTime())?"-":new Intl.DateTimeFormat("tr-TR",{dateStyle:"medium",timeStyle:"short"}).format(date);
}
function normalizeHeading(value){
  return String(value||"").replace(/^#{1,6}\s*/,"").replace(/^\*\*(.*?)\*\*:?$/,"$1").replace(/^__(.*?)__:?$/,"$1").replace(/:$/,"").trim().toLocaleLowerCase("tr-TR");
}
function parseSummary(answer){
  const aliases=new Map([
    ["dosyanın konusu","topic"],["dosyanin konusu","topic"],["son durum","status"],
    ["öne çıkan gelişmeler","developments"],["one cikan gelismeler","developments"],
    ["önemli tarihler","dates"],["onemli tarihler","dates"]
  ]);
  const sections={topic:[],status:[],developments:[],dates:[],other:[]};
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
function section(title,items,list=false){
  if(!items?.length)return "";
  const body=list
    ?`<ul>${items.slice(0,8).map(item=>`<li>${esc(item)}</li>`).join("")}</ul>`
    :items.slice(0,4).map(item=>`<p>${esc(item)}</p>`).join("");
  return `<section><h2>${esc(title)}</h2>${body}</section>`;
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
    section("Dosyanın konusu",parsed.topic)+
    section("Son durum",parsed.status)+
    section("Öne çıkan gelişmeler",parsed.developments,true)+
    section("Önemli tarihler",parsed.dates,true)||
    `<section><p>${esc(payload.answer||"Özet bulunamadı.")}</p></section>`;
  const sources=Array.isArray(payload.sources)?payload.sources:[];
  document.getElementById("sources").innerHTML=sources.length
    ?'<h2>Özet için kullanılan evraklar</h2>'+sources.map(source=>`<div class="source"><span>${esc(source.sourceRef||"-")}</span><div><strong>${esc(source.title||"Adsız evrak")}</strong><small>${esc(source.selectionReason||"Doğrulanmış kaynak")}${source.documentDate?" • "+esc(source.documentDate):""}</small></div></div>`).join("")
    :"";
}
document.getElementById("printButton").addEventListener("click",()=>window.print());
document.getElementById("closeButton").addEventListener("click",()=>window.close());
void main().then(()=>setTimeout(()=>window.print(),350));
