// Native protocol regression, not upstream CAPTCHA/account acceptance evidence.
import fs from 'node:fs';
import net from 'node:net';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [hostPath,directory,mode]=process.argv.slice(2);
const calls=[], authorizations=[];
globalThis.ego={
  listProfiles:async()=>({profiles:[{id:'Default'}]}),
  createTaskSpace:async()=>({id:71}),useTaskSpace:()=>{},
  createTab:async()=>({targetId:'owned-tab'}),
  getBrowserVersion:async()=>({currentVersion:'fixture'}),
  sendCDPMessage:raw=>{
    const m=JSON.parse(raw);calls.push(m);
    const result=m.method==='Target.attachToTarget'?{sessionId:'owned-session'}:
      m.method==='Page.getFrameTree'?{frameTree:{frame:{id:'main-frame'}}}:
      m.method==='Browser.getWindowForTarget'?{windowId:17,bounds:{windowState:'normal'}}:{};
    queueMicrotask(()=>ego.onCDPMessage(JSON.stringify({id:m.id,result})));
  },
};
const config={socket:directory+'/native.sock',ready:directory+'/ready.json',task_id:null,
  task_name:'owned',profile:'Default',interactive:mode==='interactive',
  plan:{auth:{login_origins:['https://login.example']}}};
const {start}=await import(pathToFileURL(hostPath));
const running=start(config);
while(!fs.existsSync(config.ready))await new Promise(r=>setTimeout(r,5));
const client=net.connect(config.socket);
let buffer='',serial=1000;const pending=new Map();
const command=operation=>new Promise(resolve=>{const id=++serial;pending.set(id,resolve);client.write(JSON.stringify({id,operation})+'\n');});
client.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){
  const m=JSON.parse(buffer.slice(0,n));buffer=buffer.slice(n+1);
  if(m.event==='authorize'){
    authorizations.push(m);
    // Stand-in for the Python policy: frame documents must not gain CDN scope.
    const allowed=m.url.startsWith('https://site.example/') ||
      (config.interactive && m.url.startsWith('https://login.example/'));
    client.write(JSON.stringify({id:m.id,authorized:allowed})+'\n');
  } else if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);}
}});
await new Promise(r=>client.once('connect',r));
const event=(method,params,sid='owned-session')=>ego.onCDPMessage(JSON.stringify({method,params,sessionId:sid}));
const wait=async ready=>{const end=Date.now()+1000;while(!ready() && Date.now()<end)await new Promise(r=>setTimeout(r,2));};
const paused=async(id,url,extra={},sid='owned-session')=>{
  event('Fetch.requestPaused',{requestId:id,request:{url,method:'GET'},resourceType:'Document',frameId:'child-frame',...extra},sid);
  await wait(()=>calls.some(c=>c.params.requestId===id));
  return calls.find(c=>c.params.requestId===id)?.method;
};
const attach=(id,type='iframe',parent='owned-session')=>event('Target.attachedToTarget',{
  sessionId:id+'-session',waitingForDebugger:true,targetInfo:{targetId:id,type,url:'https://login.example/'},
},parent);
try {
  assert.equal(await paused('same-origin','https://site.example/frame'),
    config.interactive?'Fetch.continueRequest':'Fetch.failRequest',
    'the actual verification iframe must be usable during human takeover');
  if(!config.interactive) {
    attach('background-frame');
    await wait(()=>calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='background-frame'));
    assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='background-frame'));
    assert(!calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger'));
  } else {
    assert.equal(await paused('approved-login','https://login.example/frame'),'Fetch.continueRequest');
    assert.equal(await paused('outside','https://outside.example/frame'),'Fetch.failRequest');
    assert.deepEqual((await command('diagnostics')).result.blocked_navigation_origins,[],
      'an optional/ad frame must not turn the entire website into a login redirect');
    assert.deepEqual((await command('diagnostics')).result.blocked_frame_origins,['https://outside.example']);
    assert(authorizations.every(a=>a.document),'frame requests use document authority, not resource/CDN authority');
    // Child success must not erase the top-level verification failure.
    await paused('top-denied','https://site.example/',{frameId:'main-frame',responseStatusCode:403});
    await paused('child-ok','https://login.example/',{responseStatusCode:200});
    assert.equal((await command('diagnostics')).result.resource_failures[0].status,403);
    attach('login-frame');
    await wait(()=>calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='login-frame-session'));
    const childCalls=calls.filter(c=>c.sessionId==='login-frame-session').map(c=>c.method);
    assert(childCalls.includes('Fetch.enable'),'child requests must be guarded before resuming');
    assert(childCalls.indexOf('Fetch.enable')<childCalls.indexOf('Runtime.runIfWaitingForDebugger'));
    assert(!calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='login-frame'));
    assert.equal(await paused('child-denied','https://outside.example/',{},'login-frame-session'),'Fetch.failRequest');
    assert.equal(await paused('child-login','https://login.example/',{},'login-frame-session'),'Fetch.continueRequest');
    attach('nested-frame','iframe','login-frame-session');
    await wait(()=>calls.some(c=>c.method==='Fetch.enable' && c.sessionId==='nested-frame-session'));
    assert(calls.some(c=>c.method==='Fetch.enable' && c.sessionId==='nested-frame-session'));
    // Popups, workers and unrelated sessions are not granted iframe permissions.
    attach('popup','page');attach('worker','worker');attach('unrelated','iframe','another-owner');
    await wait(()=>calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='worker'));
    assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='popup'));
    assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='worker'));
    assert(!calls.some(c=>c.params.targetId==='unrelated' || c.sessionId==='unrelated-session'));
    event('Target.attachedToTarget',{sessionId:'unpaused-session',waitingForDebugger:false,
      targetInfo:{targetId:'unpaused-frame',type:'iframe'}});
    await wait(()=>calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='unpaused-frame'));
    assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='unpaused-frame'));
    assert(!calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='unpaused-session'));
    for(let i=0;i<6;i++)attach('bounded-'+i);
    await wait(()=>calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='bounded-5-session'));
    attach('over-limit');
    await wait(()=>calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='over-limit'));
    assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='over-limit'));
    assert(!calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='over-limit-session'));
    event('Target.detachedFromTarget',{sessionId:'bounded-0-session'});
    attach('replacement-frame');
    await wait(()=>calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='replacement-frame-session'));
    assert(calls.some(c=>c.method==='Runtime.runIfWaitingForDebugger' && c.sessionId==='replacement-frame-session'));
    event('Target.detachedFromTarget',{sessionId:'login-frame-session'});
    const beforeDetached=authorizations.length;
    event('Fetch.requestPaused',{requestId:'orphan-frame',request:{url:'https://site.example/',method:'GET'},resourceType:'Document'},'nested-frame-session');
    await command('diagnostics');
    assert.equal(authorizations.length,beforeDetached,'detaching a parent revokes all descendant sessions');
    // The whole interaction retains one transfer budget across frame sessions.
    for(let i=0;i<17;i++)event('Network.dataReceived',{requestId:'item-'+i,dataLength:1024*1024},i%2?'owned-session':'replacement-frame-session');
    assert.equal((await command('diagnostics')).result.fatal_error,'resource_budget_exceeded');
    const beforeBudget=authorizations.length;
    assert.equal(await paused('after-budget','https://site.example/',{},'replacement-frame-session'),'Fetch.failRequest');
    assert.equal(authorizations.length,beforeBudget,'a spent shared budget cannot authorize new frame traffic');
  }
} finally {await command('close');await running;}
console.log('embedded login boundary passed');
