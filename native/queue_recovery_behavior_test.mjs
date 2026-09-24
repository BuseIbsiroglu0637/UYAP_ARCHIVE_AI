import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../background/download-manager.js',import.meta.url),'utf8');
const begin=source.indexOf('async function repairUnassignedBatches(');
const end=source.indexOf('export async function recoverSession()',begin);
let documents=Array.from({length:11934},(_,index)=>({key:String(index),status:index<11240?'verified':'queued',largeBatchNo:index<11924?Math.floor(index/500)+1:undefined}));
let session={largeCaseMode:true,finalIntegrity:{sourceCount:11924}};
let writes=0;
const context=vm.createContext({
  sessionStore:{get:async()=>session,update:async(_,changes)=>Object.assign(session,changes)},
  documentStore:{bySession:async()=>documents,putMany:async(rows)=>{writes+=rows.length; for(const row of rows) documents[Number(row.key)]=row;}},
  STATUS:{VERIFIED:'verified',SKIPPED:'skipped'},LARGE_CASE_BATCH_SIZE:500,
  recordDiagnosticEvent:async()=>{}
});
vm.runInContext(source.slice(begin,end),context);
await vm.runInContext('repairUnassignedBatches("s")',context);
assert.equal(writes,10);
assert.equal(session.unresolved,694);
assert.equal(session.total,11934);
assert.equal(session.finalIntegrity,null);
assert.ok(documents.every(d=>d.largeBatchNo>0));
await vm.runInContext('repairUnassignedBatches("s")',context);
assert.equal(writes,10,'migration must be idempotent');
assert.equal(documents.filter(d=>d.status==='verified').length,11240);
const helperStart=source.indexOf('function isUyapInvalidRequestPdfTail(');
const helperEnd=source.indexOf('\n}',helperStart)+2;
vm.runInContext('const UYAP_INVALID_REQUEST_TAIL_HEX="496e76616c69642072657175657374";'+source.slice(helperStart,helperEnd),context);
assert.equal(vm.runInContext('isUyapInvalidRequestPdfTail({errorCode:"PDF_TRAILING_DATA",pdfTrailingBytes:17})',context),false);
assert.equal(vm.runInContext('isUyapInvalidRequestPdfTail({errorCode:"PDF_TRAILING_DATA",pdfTrailingCoreHex:"496e76616c69642072657175657374"})',context),true);
console.log('Behavior tests PASS: 11934 records, batch migration, idempotency, preserved verified records, exact tail evidence');
const terminalStart=source.indexOf('function isTerminalHtmlReviewDocument(');
const terminalEnd=source.indexOf('async function normalizeTerminalHtmlReviewDocument',terminalStart);
vm.runInContext('Object.assign(STATUS,{AWAITING_BROWSER:"awaiting_browser",DOWNLOADING:"downloading",COMPLETED:"completed"}); const HTML_REVIEW_ERROR_CODES=new Set(["HTML_RESPONSE"]); function hasPreservedHtmlEvidence(){return true;}'+source.slice(terminalStart,terminalEnd),context);
for(const status of ['completed','downloading','awaiting_browser','verified','skipped']) {
  context.testDocument={status,htmlRecoveryAttempts:1,htmlRecoveryFinal:true};
  assert.equal(vm.runInContext('isTerminalHtmlReviewDocument(testDocument)',context),false,status);
}
context.testDocument={status:'retry_wait',retryClass:'deferred_html',retryExhausted:false,htmlRecoveryFinal:false,completionRecoveryPreviousErrorCode:'HTML_RESPONSE'};
assert.equal(vm.runInContext('isTerminalHtmlReviewDocument(testDocument)',context),false);
context.testDocument={status:'review_required',htmlRecoveryFinal:true,errorCode:'HTML_RESPONSE'};
assert.equal(vm.runInContext('isTerminalHtmlReviewDocument(testDocument)',context),true);
console.log('HTML recovery state behavior PASS');
