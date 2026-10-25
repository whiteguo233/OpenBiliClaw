// Native Ego host: no Playwright, no remote debugging port, no model-authored code.
import net from 'node:net';
import fs from 'node:fs';
import { SNAPSHOT_JS, COLLECT_JS, FORM_JS, SEARCH_INPUT_JS } from './ego_dom.mjs';

export async function workflowTaskSpace(ego, config) {
  const checked=value=>{if(value?.error_code)throw Error(value.error_code);return value;};
  if(config.task_id!==null) {
    const spaces=checked(await ego.listTaskSpaces()).taskSpaces;
    const task=spaces.find(x=>x.id===config.task_id && x.name===config.task_name && x.ownership==='agent' && x.profileId===config.profile);
    if(task)return task;
    // Ego drops empty task spaces on host restart. Recreate only after a
    // confirmed host-generation change, and only when both old identities are
    // absent. Renamed, user-owned, moved or reused identities remain a stop.
    if(!config.recover_missing_task || spaces.some(x=>x.id===config.task_id || x.name===config.task_name))throw Error('ego_session_identity_changed');
  }
  const profiles=checked(await ego.listProfiles()).profiles;
  if(!profiles.some(p=>p.id===config.profile))throw Error('EGO_PROFILE_NOT_FOUND');
  return checked(await ego.createTaskSpace(config.task_name,config.profile));
}

export async function startFile(path) {
  return start(JSON.parse(fs.readFileSync(path,'utf8')));
}

export async function start(config) {
  const ego = globalThis.ego;
  if (!ego || typeof ego.sendCDPMessage !== 'function') throw Error('ego_native_unavailable');
  let serial = 0, session, target, mainFrame, socket, closing = false;
  const pending = new Map(), guards = new Map(), sockets = new Set(), responseBytes = new Map();
  const diagnostic = {blocked_requests: 0, blocked_resource_origins: [],
    blocked_navigation_origins: [], resource_failures: [], unsupported_embedded_pages: 0, login_wall: false};
  let requests = 0, bytes = 0;
  let finish; const finished=new Promise(resolve=>{finish=resolve;});
  const send = value => { if(socket && !socket.destroyed) socket.write(JSON.stringify(value)+'\n'); };
  const native = value => { if(value?.error_code) throw Error(value.error_code); return value; };
  const origin = url => { try { const u=new URL(url); return ['http:','https:'].includes(u.protocol) ? u.origin : ''; } catch{return '';} };
  const noteBlock = (url, document, code) => {
    diagnostic.blocked_requests++; diagnostic.last_block_code=code;
    const key=document?'blocked_navigation_origins':'blocked_resource_origins';
    const value=origin(url); if(value && !diagnostic[key].includes(value) && diagnostic[key].length<16) diagnostic[key].push(value);
    if(document && config.plan.auth.login_origins.includes(value)) diagnostic.login_wall=true;
  };
  const rpc = (method, params={}, sid=session) => new Promise((resolve,reject)=>{
    const id=++serial;
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('ego_protocol_timeout'));}, 30000);
    pending.set(id,{resolve,reject,timer,method});
    try { ego.sendCDPMessage(JSON.stringify({id,method,params,...(sid?{sessionId:sid}:{})})); }
    catch {clearTimeout(timer);pending.delete(id);reject(Error('ego_protocol_error'));}
  });
  const approve = params => new Promise(resolve=>{
    const id=++serial, timer=setTimeout(()=>{guards.delete(id);resolve(false);},6000);
    guards.set(id,{resolve,timer}); send({event:'authorize',id,...params});
  });
  const guard = async (params, sid) => {
    const {requestId,request,resourceType,frameId,responseStatusCode,responseHeaders}=params;
    let url=request.url, allowed=false;
    const document=resourceType==='Document';
    try {
      if(document && mainFrame && frameId!==mainFrame) {
        diagnostic.unsupported_embedded_pages++;
        await rpc('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'},sid);return;
      }
      const isRedirect=responseStatusCode>=300 && responseStatusCode<400;
      const location=isRedirect ? (responseHeaders||[]).find(x=>x.name.toLowerCase()==='location')?.value : '';
      if(location) url=new URL(location,url).href;
      if(!responseStatusCode) requests++;
      const length=Number((responseHeaders||[]).find(x=>x.name.toLowerCase()==='content-length')?.value||0);
      allowed=requests<=250 && length<=4*1024*1024 && (!document || !mainFrame || frameId===mainFrame);
      if(!allowed && (requests>250 || length>4*1024*1024)) diagnostic.fatal_error='resource_budget_exceeded';
      if(allowed) allowed=await approve({url,method:request.method,document,redirect:!!location});
      if(!allowed) {
        noteBlock(url,document,'origin_or_network_blocked');
        await rpc('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'},sid); return;
      }
      if(document && responseStatusCode>=200 && responseStatusCode<300) {
        // A successful replacement document supersedes an earlier guest/error
        // page; keep script/resource errors and all access-block diagnostics.
        diagnostic.resource_failures=diagnostic.resource_failures.filter(x=>x.resource_type!=='Document');
      }
      if(responseStatusCode>=400 && diagnostic.resource_failures.length<16) {
        const row={origin:origin(url),status:responseStatusCode,resource_type:resourceType};
        if(!diagnostic.resource_failures.some(x=>JSON.stringify(x)===JSON.stringify(row))) diagnostic.resource_failures.push(row);
      }
      await rpc(responseStatusCode?'Fetch.continueResponse':'Fetch.continueRequest',{requestId},sid);
    } catch {
      try { await rpc('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'},sid); } catch {}
    }
  };
  ego.onSendCDPMessageError=(_message,code)=>{
    for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error(/^EGO_[A-Z_]+$/.test(code)?code:'ego_protocol_error'));} pending.clear();
  };
  ego.onCDPMessage=raw=>{
    try {
      const event=JSON.parse(raw);
      if(event.id) { const p=pending.get(event.id); if(!p)return; clearTimeout(p.timer);pending.delete(event.id);
        if(event.error) p.reject(Error(p.method==='Page.captureScreenshot' &&
          event.error.message==='Cannot capture a screenshot while the window is minimized.'
          ? 'ego_window_minimized' : 'ego_protocol_error'));
        else p.resolve(event.result); return; }
      if(event.method==='Fetch.requestPaused') void guard(event.params,event.sessionId);
      if(event.method==='Target.attachedToTarget' && event.params.targetInfo.targetId!==target) {
        // Never resume a new worker, popup or OOPIF outside this one owned page.
        void rpc('Target.closeTarget',{targetId:event.params.targetInfo.targetId},null).catch(()=>{});
      }
      if(event.method==='Page.javascriptDialogOpening') void rpc('Page.handleJavaScriptDialog',{accept:false}).catch(()=>{});
      if(event.method==='Network.dataReceived') { bytes+=event.params.dataLength||0;
        const id=event.params.requestId;responseBytes.set(id,(responseBytes.get(id)||0)+(event.params.dataLength||0));
        if(bytes>16*1024*1024 || responseBytes.get(id)>4*1024*1024) {diagnostic.fatal_error='resource_budget_exceeded';void rpc('Page.stopLoading').catch(()=>{});} }
    } catch {}
  };
  const evaluate=async (fn,arg=null)=>{
    const result=await rpc('Runtime.evaluate',{expression:`(${fn})(${JSON.stringify(arg)})`,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails) throw Error('ego_dom_error'); return result.result?.value;
  };
  const query = async selector => evaluate(`s=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();const style=getComputedStyle(e);return {visible:!!e.getClientRects().length && r.width>0 && r.height>0 && style.visibility!=='hidden' && style.visibility!=='collapse',href:e.href||'',x:r.x+r.width/2,y:r.y+r.height/2};}`,selector);
  const snapshot=async()=>{const value=await evaluate(SNAPSHOT_JS,{entry_url:config.plan.entry_url});return {...value,...diagnostic,login_wall:!!(value.login_wall||diagnostic.login_wall)};};
  const restoreInteractiveWindow=async()=>{
    if(!config.interactive)return;
    let win=await rpc('Browser.getWindowForTarget',{targetId:target},null);
    if(win.bounds?.windowState!=='minimized')return;
    await rpc('Browser.setWindowBounds',{windowId:win.windowId,bounds:{windowState:'normal'}},null);
    // macOS acknowledges restoration before the native window is drawable.
    // Keep this inside the command budget and never restore background pages.
    const deadline=Date.now()+3000;
    while(Date.now()<deadline) {
      win=await rpc('Browser.getWindowForTarget',{targetId:target},null);
      if(win.bounds?.windowState!=='minimized')return;
      await new Promise(resolve=>setTimeout(resolve,50));
    }
    throw Error('ego_window_unavailable');
  };
  const navigate=async url=>{
    if(!await approve({url,method:'GET',document:true,redirect:false})) throw Error('origin_not_allowed');
    const previousLoader=(await rpc('Page.getFrameTree')).frameTree.frame.loaderId;
    const result=await rpc('Page.navigate',{url});
    if(result.errorText) throw Error(diagnostic.login_wall?'login_required':'ego_navigation_failed');
    const end=Date.now()+30000;
    let stableDocument='', stableSince=0;
    while(Date.now()<end) {
      if(diagnostic.fatal_error)throw Error(diagnostic.fatal_error);
      const tree=await rpc('Page.getFrameTree');
      const state=await evaluate('()=>({ready:document.readyState,url:location.href})');
      const loader=tree.frameTree.frame.loaderId;
      const committed=result.loaderId ? loader===result.loaderId || loader!==previousLoader : state.url===new URL(url).href;
      // A fully loaded guest/interstitial document can immediately redirect.
      // Reset a bounded settling window on each document change, including a
      // replacement loader that committed before our first poll. Every request
      // and the final URL still pass the normal origin/network authorization.
      if(committed && state.url!=='about:blank' && state.ready==='complete') {
        const identity=JSON.stringify([loader,state.url]);
        if(identity!==stableDocument){stableDocument=identity;stableSince=Date.now();}
        if(Date.now()-stableSince>=1500) {
          if(!await approve({url:state.url,method:'GET',document:true,redirect:false})) throw Error('origin_not_allowed'); return;
        }
      } else {
        stableDocument=''; stableSince=0;
      }
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw Error('ego_navigation_timeout');
  };
  const fill=async arg=>evaluate(`a=>{const es=document.querySelectorAll(a.selector);if(es.length!==1)return false;const e=es[0];if(!['INPUT','TEXTAREA'].includes(e.tagName))return false;e.focus();const p=e.tagName==='INPUT'?HTMLInputElement.prototype:HTMLTextAreaElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(e,a.value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true;}`,arg);
  const close=async()=>{
    if(closing)return; closing=true;
    try {if(target) {
      // Closing the last tab can leave an empty visible window behind, so put
      // the session window away before releasing it, including login windows.
      try {
        const win=await rpc('Browser.getWindowForTarget',{targetId:target},null);
        await rpc('Browser.setWindowBounds',{windowId:win.windowId,bounds:{windowState:'minimized'}},null);
      } catch {}
      await rpc('Target.closeTarget',{targetId:target},null);
    }}catch{}
    for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('ego_session_closed'));}pending.clear();
    for(const g of guards.values()){clearTimeout(g.timer);g.resolve(false);}guards.clear();
    for(const s of sockets)s.end();server.close();clearTimeout(lifetime);finish();
  };
  const commands={
    snapshot, diagnostics:async()=>({...diagnostic}), screenshot:async()=>{
      for(let attempt=0;attempt<2;attempt++) {
        await restoreInteractiveWindow();
        try {return (await rpc('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data;}
        catch(error) {
          // Another background session can minimize this shared native window
          // after the ready check. Retry only this proven, read-only failure.
          if(!config.interactive || error.message!=='ego_window_minimized' || attempt===1)throw error;
        }
      }
    },
    navigate:async a=>navigate(a.url), query:async a=>query(a.selector),
    safe_search:async a=>evaluate(SEARCH_INPUT_JS,a.selector),
    form:async a=>evaluate(FORM_JS,a.selector), collect:async a=>evaluate(COLLECT_JS,a), fill,
    click:async a=>{let x=a.x,y=a.y;if(a.selector){const q=await query(a.selector);if(!q?.visible)throw Error('invalid_action');x=q.x;y=q.y;}
      if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||y<0||x>1280||y>900)throw Error('invalid_action');
      await rpc('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
      await rpc('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});},
    key:async a=>{const keys={Enter:13,Tab:9,Escape:27,Backspace:8,ArrowDown:40,ArrowUp:38,Space:32};if(!keys[a.key])throw Error('invalid_action');
      const key=a.key==='Space'?' ':a.key; await rpc('Input.dispatchKeyEvent',{type:'keyDown',key,windowsVirtualKeyCode:keys[a.key]});await rpc('Input.dispatchKeyEvent',{type:'keyUp',key,windowsVirtualKeyCode:keys[a.key]});},
    close:async()=>{await close();return true;},
  };
  const server=net.createServer(connection=>{
    if(socket){connection.destroy();return;} socket=connection;sockets.add(connection);let buffer='';
    connection.on('data',data=>{buffer+=data;if(buffer.length>256*1024){connection.destroy();return;}
      let index;while((index=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,index);buffer=buffer.slice(index+1);
        let msg;try{msg=JSON.parse(line);}catch{connection.destroy();return;}
        if(msg.authorized!==undefined){const g=guards.get(msg.id);if(g){clearTimeout(g.timer);guards.delete(msg.id);g.resolve(msg.authorized===true);}continue;}
        // Acknowledge close before tearing down the socket: the caller's 3s
        // close budget must not fail merely because this reply raced teardown,
        // otherwise every run looks like a leaked tab and skips the idle grace.
        if(msg.operation==='close') {send({id:msg.id,result:true});setTimeout(()=>void close(),0);continue;}
        const fn=Object.hasOwn(commands,msg.operation)?commands[msg.operation]:null;
        Promise.resolve().then(()=>{if(!fn)throw Error('invalid_operation');if(diagnostic.fatal_error && !['snapshot','diagnostics','close'].includes(msg.operation))throw Error(diagnostic.fatal_error);return fn(msg.arguments||{});}).then(result=>{if(diagnostic.fatal_error && !['snapshot','diagnostics','close'].includes(msg.operation))throw Error(diagnostic.fatal_error);send({id:msg.id,result});}).catch(e=>send({id:msg.id,error:/^[A-Za-z_]+$/.test(e.message)?e.message:'ego_operation_failed'}));
      }});
    connection.on('close',()=>void close());
    connection.on('error',()=>void close());
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(config.socket,resolve);});
  fs.chmodSync(config.socket,0o600);
  const lifetime=setTimeout(()=>void close(),config.interactive?15*60*1000:120000);
  let task;
  try {
    task=await workflowTaskSpace(ego,config);
    ego.useTaskSpace(task.id);
    const tab=native(await ego.createTab('about:blank'));target=tab.targetId;
    session=(await rpc('Target.attachToTarget',{targetId:target,flatten:true},null)).sessionId;
    // Background discovery must never put a page on screen. Interactive login
    // keeps its window because the user has to see and drive it.
    if(!config.interactive) {
      try {
        const win=await rpc('Browser.getWindowForTarget',{targetId:target},null);
        await rpc('Browser.setWindowBounds',{windowId:win.windowId,bounds:{windowState:'minimized'}},null);
      } catch {}
    } else await restoreInteractiveWindow();
    await rpc('Page.enable'); await rpc('Network.enable');
    await rpc('Network.setBypassServiceWorker',{bypass:true});await rpc('Network.setCacheDisabled',{cacheDisabled:true});
    await rpc('Network.setBlockedURLs',{urls:['ws://*','wss://*','file://*','ftp://*']});
    await rpc('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:true,flatten:true});
    await rpc('Page.setDownloadBehavior',{behavior:'deny'});
    await rpc('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
    mainFrame=(await rpc('Page.getFrameTree')).frameTree.frame.id;
    await rpc('Page.addScriptToEvaluateOnNewDocument',{source:"Object.defineProperty(globalThis,'WebSocket',{value:class{constructor(){throw Error('WebSocket disabled');}},configurable:false});"});
    await rpc('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'},{urlPattern:'*',requestStage:'Response'}]});
    fs.writeFileSync(config.ready,JSON.stringify({task_id:task.id,task_name:config.task_name,version:native(await ego.getBrowserVersion()).currentVersion}),{mode:0o600});
  }catch(e){fs.writeFileSync(config.ready,JSON.stringify({task_id:task?.id,error:/^[A-Za-z_]+$/.test(e.message)?e.message:'ego_host_failed'}),{mode:0o600});await close();}
  await finished;
}
