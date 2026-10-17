'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'workspace/workspace.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'workspace/index.html'), 'utf8');
function makeUi(){
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {hidden:false,disabled:false,textContent:'',innerHTML:'',title:''});
    return elements.get(id);
  };
  const actions = Array.from({length:5},()=>({disabled:false,title:''}));
  const state = { aiCurrentResult:{id:'previous'} };
  const ctx={ $:get, state, n:x=>Number(x||0), document:{querySelectorAll:q=>q==='#view-ai [data-ai-workflow]'?actions:[]}};
  vm.createContext(ctx);
  const helper=js.slice(js.indexOf('function renderAiCaseAvailability('),js.indexOf('async function renderAiWorkspace('));
  assert.ok(helper.startsWith('function renderAiCaseAvailability('),'P2 availability function must exist');
  vm.runInContext(helper,ctx);
  return {ctx,get,actions,state};
}
test('Incomplete cases show a persistent AI-disabled warning and cannot launch a workflow',()=>{
  const {ctx,get,actions,state}=makeUi();
  ctx.renderAiCaseAvailability({readyForAi:false,physicalReadyDocuments:3949,totalDocuments:3951,problemDocuments:2});
  assert.equal(get('aiIncompleteWarning').hidden,false);
  assert.match(get('aiIncompleteWarningDetails').textContent,/3\.949 \/ 3\.951/);
  assert.match(get('aiIncompleteWarningDetails').textContent,/2 kayıt/);
  assert.ok(actions.every(b=>b.disabled));
  assert.equal(get('aiFreeformMode').disabled,true);
  assert.equal(get('aiPrompt').disabled,true);
  assert.equal(get('aiResultPanel').hidden,true);
  assert.equal(state.aiCurrentResult,null);
});
test('Complete cases hide the warning and restore AI workflow controls',()=>{
  const {ctx,get,actions}=makeUi();
  ctx.renderAiCaseAvailability({readyForAi:false,physicalReadyDocuments:75,totalDocuments:76,problemDocuments:1});
  ctx.renderAiCaseAvailability({readyForAi:true,physicalReadyDocuments:76,totalDocuments:76,problemDocuments:0});
  assert.equal(get('aiIncompleteWarning').hidden,true);
  assert.ok(actions.every(b=>!b.disabled));
  assert.equal(get('aiFreeformMode').disabled,false);
  assert.equal(get('aiPrompt').disabled,false);
});
test('Unknown readiness keeps controls closed',()=>{
  const {ctx,get,actions}=makeUi();
  ctx.renderAiCaseAvailability(null);
  assert.equal(get('aiIncompleteWarning').hidden,true);
  assert.ok(actions.every(b=>b.disabled));
});
test('Send button refuses incomplete case even when provider and index appear ready',()=>{
  const ctx={state:{aiSendGate:{caseSelected:true,caseReady:false,indexReady:true,providerReady:true}},
    $:id=>({get disabled(){return this._disabled},set disabled(v){this._disabled=v},get textContent(){return this._textContent},set textContent(v){this._textContent=v}}),
    aiWorkflowNeedsProvider:()=>true,aiSendLabel:()=>"Soruyu gönder"};
  const send={disabled:false,textContent:''};ctx.$=()=>send;
  vm.createContext(ctx);
  const code=js.slice(js.indexOf('function updateAiSendButton('),js.indexOf('function selectedAiArchiveCase('));
  vm.runInContext(code,ctx);
  ctx.updateAiSendButton();
  assert.equal(send.disabled,true);
  assert.match(send.textContent,/AI KAPALI/);
  ctx.state.aiSendGate.caseReady=true;
  ctx.updateAiSendButton();
  assert.equal(send.disabled,false);
});
test('Warning is accessible and visible in the AI workspace document',()=>{
  assert.match(html,/id="aiIncompleteWarning"[^>]*role="alert"/);
  assert.match(html,/id="aiIncompleteWarningDetails"/);
});
