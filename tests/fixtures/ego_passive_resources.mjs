// Native protocol regression only; this is not real-site acceptance evidence.
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
const config={socket:directory+'/native.sock',ready:directory+'/ready.json',task_id:null,task_name:'owned',profile:'Default',interactive:mode==='interactive',plan:{auth:{login_origins:[]}}};
const {start}=await import(pathToFileURL(hostPath));
const running=start(config);
while(!fs.existsSync(config.ready))await new Promise(r=>setTimeout(r,5));
const client=net.connect(config.socket);
let buffer='',serial=1000;const pending=new Map();
const command=operation=>new Promise(resolve=>{const id=++serial;pending.set(id,resolve);client.write(JSON.stringify({id,operation})+'\n');});
client.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){const m=JSON.parse(buffer.slice(0,n));buffer=buffer.slice(n+1);
  if(m.event==='authorize'){
    authorizations.push(m);client.write(JSON.stringify({id:m.id,authorized:m.url.startsWith('https://allowed.example/')})+'\n');
  } else if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);}
}});
await new Promise(r=>client.once('connect',r));
const paused=async (id,type,extra={},url='https://allowed.example/resource')=>{
  ego.onCDPMessage(JSON.stringify({method:'Fetch.requestPaused',sessionId:'owned-session',params:{requestId:id,request:{url,method:'GET'},resourceType:type,frameId:'main-frame',...extra}}));
  const deadline=Date.now()+1000;
  while(!calls.some(c=>c.params.requestId===id) && Date.now()<deadline)await new Promise(r=>setTimeout(r,1));
  return calls.find(c=>c.params.requestId===id)?.method;
};
for(const type of ['Image','Media','Font']) {
  assert.equal(await paused(type,type),config.interactive?'Fetch.continueRequest':'Fetch.failRequest');
}
assert.equal(authorizations.length,3,'passive requests still observe the normal origin policy');
assert.equal(await paused('unapproved-image','Image',{},'https://unapproved.example/image'),'Fetch.failRequest');
assert.deepEqual((await command('diagnostics')).result.blocked_resource_origins,['https://unapproved.example'],
  'retain observed CDN scope for a later authorized interactive login');
for(const type of ['Document','Script','Stylesheet','XHR','Fetch']) {
  assert.equal(await paused(type,type),'Fetch.continueRequest','text and dynamic page loading must remain available');
}
if(!config.interactive) {
  for(let i=0;i<260;i++)assert.equal(await paused('image-'+i,'Image'),'Fetch.failRequest');
  assert.equal(await paused('large-image','Image',{responseStatusCode:200,responseHeaders:[{name:'Content-Length',value:'5000000'}]}),'Fetch.failRequest');
  const d=(await command('diagnostics')).result;
  assert.equal(d.fatal_error,undefined,'discarded passive resources must not exhaust actual network budgets');
  assert.equal(await paused('next-script','Script'),'Fetch.continueRequest');
  for(let i=0;i<17;i++)ego.onCDPMessage(JSON.stringify({method:'Network.dataReceived',sessionId:'owned-session',params:{requestId:'xhr-'+i,dataLength:1024*1024}}));
  assert.equal((await command('diagnostics')).result.fatal_error,'resource_budget_exceeded','the 16 MiB run budget remains enforced');
} else {
  assert.equal(await paused('large-image','Image',{responseStatusCode:200,responseHeaders:[{name:'Content-Length',value:'5000000'}]}),'Fetch.failRequest');
  assert.equal((await command('diagnostics')).result.fatal_error,'resource_budget_exceeded','interactive image transfers keep the 4 MiB response limit');
}
await command('close');await running;
console.log('passive resource policy passed');
