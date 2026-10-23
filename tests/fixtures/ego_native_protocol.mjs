// Deterministic native-binding transport fixture; never a real Ego acceptance.
import fs from 'node:fs';
import net from 'node:net';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const [hostPath,directory]=process.argv.slice(2);
const calls=[];
let focused=false, hidden=true, documentUrl='https://allowed.example/old', loader='old', navigation=false, polls=0, redirectMode='', generation=0;
class Input {set value(v){this.stored=v;} get value(){return this.stored;}}
const input=new Input();Object.assign(input,{tagName:'INPUT',getClientRects:()=>[{}],getBoundingClientRect:()=>({x:0,y:0,width:20,height:10}),focus:()=>{focused=true;},dispatchEvent:()=>{},getAttribute:()=>null,classList:[],children:[],querySelectorAll:()=>[]});
const body={...input,tagName:'BODY',querySelectorAll:()=>[]};
const document={readyState:'complete',title:'Fixture',body,querySelector:selector=>selector.startsWith('main,')?body:input,querySelectorAll:selector=>selector==='input'||selector==='input[type=password]'?[input]:[]};
const context={document,location:{get href(){return documentUrl;}},URL,HTMLInputElement:Input,HTMLTextAreaElement:Input,Event:class{},getComputedStyle:()=>({visibility:hidden?'hidden':'visible'})};
globalThis.ego={
  listProfiles:async()=>({profiles:[{id:'Default'}]}),
  createTaskSpace:async()=>({id:71}),useTaskSpace:()=>{},
  createTab:async()=>({targetId:'owned-tab'}),getBrowserVersion:async()=>({currentVersion:'fixture'}),
  sendCDPMessage:raw=>{const msg=JSON.parse(raw);calls.push(msg);
    if(msg.method==='Page.navigate'){navigation=true;polls=0;generation++;redirectMode=msg.params.url.includes('redirect')?msg.params.url:'';loader='old';documentUrl='https://allowed.example/old';}
    if(msg.method==='Page.getFrameTree' && navigation && ++polls>=2){loader='new-'+generation;documentUrl='https://allowed.example/new';}
    if(redirectMode && (polls>=5 || redirectMode.endsWith('fast-redirect'))){loader='redirected-'+generation;documentUrl='https://allowed.example/topics';}
    const result=msg.method==='Target.attachToTarget'?{sessionId:'owned-session'}:
      msg.method==='Page.getFrameTree'?{frameTree:{frame:{id:'main-frame',loaderId:loader}}}:
      msg.method==='Page.navigate'?{loaderId:'new-'+generation}:
      msg.method==='Runtime.evaluate'?{result:{value:vm.runInNewContext(msg.params.expression,context)}}:{};
    queueMicrotask(()=>ego.onCDPMessage(JSON.stringify({id:msg.id,result})));},
};
const config={socket:directory+'/native.sock',ready:directory+'/ready.json',task_id:null,task_name:'owned',profile:'Default',interactive:false,plan:{auth:{login_origins:[]}}};
const {start}=await import(pathToFileURL(hostPath));
const running=start(config);
while(!fs.existsSync(config.ready))await new Promise(r=>setTimeout(r,5));
const client=net.connect(config.socket);
let buffer='';const authorizations=[],results=new Map();let serial=1000;
const command=(operation,args={})=>new Promise((resolve,reject)=>{const id=++serial;results.set(id,{resolve,reject});client.write(JSON.stringify({id,operation,arguments:args})+'\n');});
client.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){const m=JSON.parse(buffer.slice(0,n));buffer=buffer.slice(n+1);
  if(m.event==='authorize'){authorizations.push(m);client.write(JSON.stringify({id:m.id,authorized:m.url.startsWith('https://allowed.example/')})+'\n');}
  else if(results.has(m.id)){const p=results.get(m.id);results.delete(m.id);m.error?p.reject(Error(m.error)):p.resolve(m.result);}}});
await new Promise(r=>client.once('connect',r));
assert.equal((await command('query',{selector:'input'})).visible,false);
assert.equal((await command('snapshot')).login_wall,false);
hidden=false;await command('fill',{selector:'input',value:'fixture only'});assert(focused);
await command('navigate',{url:'https://allowed.example/new'});assert(polls>=2);assert.equal(documentUrl,'https://allowed.example/new');
// A complete guest interstitial can replace itself after initial commit.
await command('navigate',{url:'https://allowed.example/redirect'});
assert.equal(documentUrl,'https://allowed.example/topics','do not observe the complete guest interstitial');
// Redirect may commit before the first navigation poll sees the initial loader.
await command('navigate',{url:'https://allowed.example/fast-redirect'});
assert.equal(documentUrl,'https://allowed.example/topics');
const paused=(id,url,extra={})=>ego.onCDPMessage(JSON.stringify({method:'Fetch.requestPaused',sessionId:'owned-session',params:{requestId:id,request:{url,method:'GET'},resourceType:'Document',frameId:'main-frame',...extra}}));
const waitResponse=async id=>{
  const until=Date.now()+1000;
  while(!calls.some(c=>c.method==='Fetch.continueResponse' && c.params.requestId===id) && Date.now()<until)await new Promise(r=>setTimeout(r,5));
};
paused('failed-script','https://allowed.example/app.js',{resourceType:'Script',responseStatusCode:404,responseHeaders:[]});
await waitResponse('failed-script');
paused('guest-page','https://allowed.example/',{responseStatusCode:403,responseHeaders:[]});
await waitResponse('guest-page');
assert((await command('diagnostics')).resource_failures.some(x=>x.status===403));
paused('final-page','https://allowed.example/',{responseStatusCode:200,responseHeaders:[]});
await waitResponse('final-page');
assert.deepEqual((await command('diagnostics')).resource_failures,[{origin:'https://allowed.example',status:404,resource_type:'Script'}],'successful final document supersedes only guest response');
paused('allowed','https://allowed.example/');
paused('private','http://127.0.0.1/private');
paused('redirect','https://allowed.example/',{responseStatusCode:302,responseHeaders:[{name:'Location',value:'http://127.0.0.1/private'}]});
paused('response','https://allowed.example/',{responseStatusCode:200,responseHeaders:[]});
paused('iframe','https://ads.example/',{frameId:'child-frame'});
paused('oversize','https://allowed.example/',{responseStatusCode:200,responseHeaders:[{name:'Content-Length',value:'5000000'}]});
const until=Date.now()+3000;
while(calls.filter(c=>['Fetch.continueRequest','Fetch.continueResponse','Fetch.failRequest'].includes(c.method)).length<9 && Date.now()<until)await new Promise(r=>setTimeout(r,5));
const action=id=>calls.find(c=>c.params.requestId===id)?.method;
assert.equal(action('allowed'),'Fetch.continueRequest');
assert.equal(action('private'),'Fetch.failRequest');
assert.equal(action('redirect'),'Fetch.failRequest');
assert.equal(action('response'),'Fetch.continueResponse');
assert.equal(action('oversize'),'Fetch.failRequest');
assert.equal(action('iframe'),'Fetch.failRequest');
const diagnostics=await command('diagnostics');assert.equal(diagnostics.unsupported_embedded_pages,1);assert(!diagnostics.blocked_navigation_origins.includes('https://ads.example'));assert.equal(diagnostics.fatal_error,'resource_budget_exceeded');
assert(authorizations.some(x=>x.url==='http://127.0.0.1/private' && x.redirect));
client.write(JSON.stringify({id:999,operation:'close'})+'\n');
await running;
assert(calls.some(c=>c.method==='Target.closeTarget' && c.params.targetId==='owned-tab'));
console.log(JSON.stringify({paused_requests:9,redirect_blocked:true,closed_owned_tab:true}));
