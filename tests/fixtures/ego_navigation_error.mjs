// Native protocol fixture only; real website evidence lives in acceptance logs.
import fs from 'node:fs';
import net from 'node:net';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [hostPath,directory]=process.argv.slice(2);
let errorText='net::ERR_CONNECTION_CLOSED', navigations=0;
const calls=[];
globalThis.ego={
  listProfiles:async()=>({profiles:[{id:'Default'}]}),
  createTaskSpace:async()=>({id:71}),useTaskSpace:()=>{},
  createTab:async()=>({targetId:'owned-tab'}),
  getBrowserVersion:async()=>({currentVersion:'fixture'}),
  sendCDPMessage:raw=>{
    const message=JSON.parse(raw);calls.push(message);
    if(message.method==='Page.navigate')navigations++;
    const result=message.method==='Target.attachToTarget'?{sessionId:'owned-session'}:
      message.method==='Page.getFrameTree'?{frameTree:{frame:{id:'main-frame',loaderId:'old'}}}:
      message.method==='Browser.getWindowForTarget'?{windowId:17,bounds:{windowState:'minimized'}}:
      message.method==='Page.navigate'?{errorText}:{};
    queueMicrotask(()=>ego.onCDPMessage(JSON.stringify({id:message.id,result})));
  },
};
const config={socket:directory+'/native.sock',ready:directory+'/ready.json',identity:directory+'/identity.json',task_id:null,task_name:'owned',profile:'Default',interactive:false,plan:{auth:{login_origins:[]}}};
const {start}=await import(pathToFileURL(hostPath));
const running=start(config);
while(!fs.existsSync(config.ready))await new Promise(r=>setTimeout(r,5));
const client=net.connect(config.socket);
let buffer='', serial=1000;const pending=new Map();
const command=operation=>new Promise(resolve=>{const id=++serial;pending.set(id,resolve);client.write(JSON.stringify({id,operation,arguments:{url:'https://allowed.example/'}})+'\n');});
client.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){const m=JSON.parse(buffer.slice(0,n));buffer=buffer.slice(n+1);
  if(m.event==='authorize')client.write(JSON.stringify({id:m.id,authorized:true})+'\n');
  else if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);}
}});
await new Promise(r=>client.once('connect',r));
const failed=await command('navigate');
assert.equal(failed.error,'ego_navigation_failed');
assert.equal(failed.navigation_error,'net::ERR_CONNECTION_CLOSED');
assert.equal((await command('diagnostics')).result.navigation_error,'net::ERR_CONNECTION_CLOSED');
errorText='net::ERR_CONNECTION_CLOSED https://private.example/?token=secret';
const unsafe=await command('navigate');
assert.equal(unsafe.error,'ego_navigation_failed');
assert(!Object.hasOwn(unsafe,'navigation_error'),'never relay browser error text or URLs');
assert(!Object.hasOwn((await command('diagnostics')).result,'navigation_error'),'no stale reason from a previous navigation');
assert.equal(navigations,2,'do not introduce ineffective retries or change the route');
assert(calls.some(c=>c.method==='Fetch.enable'),'request policy stays enabled');
await command('close');await running;
console.log('safe navigation errors passed');
