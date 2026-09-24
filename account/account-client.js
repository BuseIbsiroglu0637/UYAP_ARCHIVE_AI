(() => {
  "use strict";
  const CONFIG = globalThis.UYAP_ACCOUNT_CONFIG || {};
  const STORAGE_KEY = "uyapAccountSessionV1";
  const DEVICE_KEY = "uyapDeviceIdentityV1";
  const SNAPSHOT_KEY = "uyapAccountSnapshotV1";
  const DEFAULT_ENTITLEMENTS = Object.freeze({
    archive: true,
    archive_local: true,
    archive_final_package: true,
    ai: false,
    ai_summary: false,
    ai_timeline: false,
    ai_search: false,
    multi_device: false,
    office_users: false
  });

  const configured = () => Boolean(CONFIG.enabled && CONFIG.supabaseUrl && CONFIG.publishableKey);
  const cleanUrl = () => String(CONFIG.supabaseUrl || "").replace(/\/+$/, "");
  const headers = (token, extra={}) => ({
    apikey: CONFIG.publishableKey || "",
    ...(token ? {Authorization:`Bearer ${token}`} : {}),
    "Content-Type":"application/json",
    ...extra
  });
  async function request(path, {method="GET", token=null, body=null}={}) {
    if (!configured()) throw new Error("Hesap servisi henüz yapılandırılmadı.");
    const res = await fetch(`${cleanUrl()}${path}`, {method, headers:headers(token), body:body==null?undefined:JSON.stringify(body)});
    let data=null; const text=await res.text();
    try { data=text?JSON.parse(text):null; } catch { data=text; }
    if (!res.ok) {
      const msg=data?.msg||data?.message||data?.error_description||data?.error||`HTTP ${res.status}`;
      throw new Error(String(msg));
    }
    return data;
  }
  async function loadSession(){ return (await chrome.storage.local.get(STORAGE_KEY))[STORAGE_KEY] || null; }
  async function saveSession(session){ await chrome.storage.local.set({[STORAGE_KEY]:session}); return session; }
  async function clearSession(){ await chrome.storage.local.remove([STORAGE_KEY,SNAPSHOT_KEY]); }
  function sessionExpired(s){ return !s?.expires_at || (Number(s.expires_at)*1000) < Date.now()+30000; }
  async function refreshSession(s){
    if (!s?.refresh_token) return null;
    const data=await request('/auth/v1/token?grant_type=refresh_token',{method:'POST',body:{refresh_token:s.refresh_token}});
    return saveSession(normalizeSession(data));
  }
  function normalizeSession(data){
    const access=data?.access_token, refresh=data?.refresh_token, expires=data?.expires_at || (data?.expires_in?Math.floor(Date.now()/1000)+Number(data.expires_in):null);
    if(!access) return null;
    return {access_token:access,refresh_token:refresh||null,expires_at:expires,user:data.user||null,token_type:data.token_type||'bearer'};
  }
  async function getSession({refresh=true}={}){
    let s=await loadSession();
    if(s && refresh && sessionExpired(s)) { try{s=await refreshSession(s);}catch{await clearSession();s=null;} }
    return s;
  }
  async function signIn(email,password){
    const data=await request('/auth/v1/token?grant_type=password',{method:'POST',body:{email,password}});
    const s=normalizeSession(data); await saveSession(s); return s;
  }
  async function signUp(email,password,fullName=''){
    const data=await request('/auth/v1/signup',{method:'POST',body:{email,password,data:{full_name:fullName}}});
    const s=normalizeSession(data); if(s) await saveSession(s); return {session:s,user:data?.user||null};
  }
  async function sendPasswordReset(email){
    return request('/auth/v1/recover',{method:'POST',body:{email}});
  }
  async function signOut(){
    const s=await loadSession();
    try{ if(s?.access_token && configured()) await request('/auth/v1/logout',{method:'POST',token:s.access_token,body:{}}); }catch{}
    await clearSession();
  }
  async function getUser(){
    const s=await getSession(); if(!s?.access_token) return null;
    try{return await request('/auth/v1/user',{token:s.access_token});}catch{return null;}
  }
  async function getDeviceIdentity(){
    const obj=(await chrome.storage.local.get(DEVICE_KEY))[DEVICE_KEY]; if(obj?.id)return obj;
    const bytes=new Uint8Array(16); crypto.getRandomValues(bytes);
    const id=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
    const created={id,name:`${navigator.platform||'Windows'} cihazı`,platform:navigator.platform||'unknown',createdAt:new Date().toISOString()};
    await chrome.storage.local.set({[DEVICE_KEY]:created}); return created;
  }
  async function rpc(name,args={},token=null){
    const s=token?{access_token:token}:await getSession(); if(!s?.access_token) throw new Error('Oturum açmanız gerekiyor.');
    return request(`/rest/v1/rpc/${encodeURIComponent(name)}`,{method:'POST',token:s.access_token,body:args});
  }
  async function loadAccountSnapshot({force=false}={}){
    if(!configured()) return {mode:'local',configured:false,authenticated:false,plan:{code:'local',name:'Yerel Mod'},entitlements:{...DEFAULT_ENTITLEMENTS}};
    const s=await getSession();
    if(!s?.access_token) return {mode:'cloud',configured:true,authenticated:false,plan:null,entitlements:{...DEFAULT_ENTITLEMENTS}};
    if(!force){const cached=(await chrome.storage.local.get(SNAPSHOT_KEY))[SNAPSHOT_KEY];if(cached?.fetchedAt && Date.now()-new Date(cached.fetchedAt).getTime()<5*60*1000)return cached;}
    try{
      const data=await rpc('get_account_snapshot',{},s.access_token);
      const snap={...(data||{}),configured:true,authenticated:true,fetchedAt:new Date().toISOString(),entitlements:{...DEFAULT_ENTITLEMENTS,...(data?.entitlements||{})}};
      await chrome.storage.local.set({[SNAPSHOT_KEY]:snap}); return snap;
    }catch(e){
      const user=await getUser();
      return {mode:'cloud',configured:true,authenticated:true,user,plan:{code:'unknown',name:'Hesap'},entitlements:{...DEFAULT_ENTITLEMENTS},snapshotError:String(e?.message||e)};
    }
  }
  async function registerDevice({extensionVersion='',agentVersion=''}={}){
    if(!configured()) return null;
    const d=await getDeviceIdentity();
    return rpc('register_device',{p_device_id:d.id,p_device_name:d.name,p_platform:d.platform,p_extension_version:extensionVersion,p_agent_version:agentVersion||''});
  }
  globalThis.UYAPAccount=Object.freeze({configured,config:CONFIG,signIn,signUp,signOut,sendPasswordReset,getSession,getUser,getDeviceIdentity,loadAccountSnapshot,registerDevice,DEFAULT_ENTITLEMENTS});
})();
