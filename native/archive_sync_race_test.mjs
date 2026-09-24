import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../background/download-manager.js',import.meta.url),'utf8');
const stores=fs.readFileSync(new URL('../background/store.js',import.meta.url),'utf8');
let rows=Array.from({length:1500},(_,i)=>({key:String(i),documentId:String(i),sessionId:'s',status:'queued',largeBatchNo:Math.floor(i/500)+1}));
let session={sessionId:'s',largeCaseMode:true,largeCaseTotalBatches:3,downloadsRoot:'C:/UYAP',total:1500};
let calls=0, active=0, peak=0;
const context=vm.createContext({console,Date,Map,Set,Promise,
  STATUS:{QUEUED:'queued',VERIFIED:'verified',SKIPPED:'skipped'},
  LOCAL_ARCHIVE_RECONCILIATION_VERSION:5,LAST_DOWNLOADS_ROOT_KEY:'root',
  chrome:{storage:{local:{get:async()=>({}),set:async()=>{}}}},
  sessionStore:{get:async()=>({...session}),update:async(_,p)=>Object.assign(session,p)},
  documentStore:{bySession:async()=>rows.map(r=>({...r})),putMany:async updates=>{for(const r of updates)rows[Number(r.key)]={...r};}},
  sourceFingerprint:()=> 'fingerprint',buildTargetPath:()=> 'path',
  invalidateArchiveRegistryCache:()=>{},withoutRecoveredNativeWarnings:()=>[],
  recordDiagnosticEvent:async()=>{},reusableLocalArchiveScan:()=>false,
  askNativeAgent:async({documents})=>{
    calls++; active++; peak=Math.max(peak,active); await new Promise(resolve=>setTimeout(resolve,2)); active--;
    return {ok:true,unchanged:documents.map(d=>d.documentId),physicalProofs:Object.fromEntries(documents.map(d=>[d.documentId,{level:'sha256',pathExists:true,sha256:'valid-hash',size:123}]))};
  }
});
vm.runInContext(source.slice(source.indexOf('function normalizeArchiveRootKey('),source.indexOf('function sessionValidationCount(')),context);
vm.runInContext(source.slice(source.indexOf('const localArchiveSessionTasks'),source.indexOf('async function ensureLocalArchiveBatchReconciled(')),context);
await vm.runInContext('Promise.all([1,2,3,2].map(largeBatchNo=>syncSessionWithLocalArchive("s",{largeBatchNo})))',context);
assert.equal(calls,3,'duplicate batch request must reuse completed proof');
assert.equal(peak,1,'same-session reconciliation must be serialized');
assert.equal(rows.filter(r=>r.status==='skipped').length,1500);
assert.equal(session.localArchiveRecognizedCount,1500);
assert.deepEqual([...session.localArchiveReconciledBatches],[1,2,3]);

// Execute the actual runtime field whitelist, then reproduce a native proof
// arriving while the UYAP DOM scan is outstanding.
context.patchMany=async(_,patches)=>{for(const {key,changes} of patches)Object.assign(rows[Number(key)],changes);};
vm.runInContext('const runtimeAdapter={'+stores.slice(stores.indexOf('  patchRuntimeMany:'),stores.indexOf('  get: (key)'))+'}; documentStore.patchRuntimeMany=runtimeAdapter.patchRuntimeMany;',context);
rows=[{key:'0',documentId:'0',status:'queued',largeBatchNo:1}];
session.largeCaseCurrentBatch=1;
context.scanCurrentCaseDocuments=async()=>{
  await Promise.resolve();
  Object.assign(rows[0],{status:'skipped',sha256:'fresh-proof',localArchiveMatchState:'found'});
  return [{documentId:'0',frameId:7}];
};
context.probeSessionSourceContext=async()=>({matches:true});
context.normalizedBindingValue=value=>String(value||'');
context.semanticRuntimeBindingKey=()=>'';
const start=source.indexOf('async function refreshSessionRuntimeBindings(');
const end=source.indexOf('\nasync function ',start+10);
vm.runInContext(source.slice(start,end),context);
await vm.runInContext('refreshSessionRuntimeBindings("s",1)',context);
assert.equal(rows[0].status,'skipped','DOM refresh must not undo concurrent native proof');
assert.equal(rows[0].sha256,'fresh-proof');
assert.equal(rows[0].frameId,7);

// A failed batch is not cached as complete; the next call must retry it.
rows=[{key:'0',documentId:'0',status:'queued',largeBatchNo:4}];
session.largeCaseTotalBatches=4;
context.askNativeAgent=async()=>({ok:false,error:'test failure'});
await vm.runInContext('syncSessionWithLocalArchive("s",{largeBatchNo:4})',context);
assert.ok(!session.localArchiveReconciledBatches.includes(4));
context.askNativeAgent=async()=>({ok:true,unchanged:['0'],physicalProofs:{'0':{level:'sha256',pathExists:false,sha256:'hash'}}});
await vm.runInContext('syncSessionWithLocalArchive("s",{largeBatchNo:4})',context);
assert.equal(rows[0].status,'queued','missing bytes must never count as archived');
console.log('PASS: 1500 records; serialized/deduplicated batches; concurrent DOM/proof preservation; failed-batch retry; physical-proof guard.');
