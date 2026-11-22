// Native-method fixtures only. The fixed host runs over a real loopback socket.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [hostPath,ready,auth_token,identity]=process.argv.slice(2);
let spaces=0;
let createdTab=false;
globalThis.ego={
  listProfiles:async()=>({profiles:[{id:'Default'}]}),
  createTaskSpace:async()=>({id:++spaces}),
  useTaskSpace:()=>{},
  closeTaskSpace:async()=>{},
  createTab:async()=>{createdTab=true;return {targetId:'owned-tab'};},
  getBrowserVersion:async()=>({currentVersion:'0.6.2.7'}),
  sendCDPMessage:raw=>{
    const {id,method}=JSON.parse(raw);
    assert(!method.startsWith('Browser.'));
    const result={
      'Target.attachToTarget':{sessionId:'owned-session'},
      'Page.getFrameTree':{frameTree:{frame:{id:'main'}}},
      'Page.captureScreenshot':{data:'fixture-png'},
    }[method] ?? {};
    queueMicrotask(()=>ego.onCDPMessage(JSON.stringify({id,result})));
  },
};
const {start}=await import(pathToFileURL(hostPath));
await start({
  ready,auth_token,headless:true,windows_host:{
    pid: process.ppid + (identity==='wrong_parent' ? 1 : 0),
    executable: identity==='wrong_image' ? process.execPath + '.other' : process.execPath,
  },
  task_id:null,task_name:'owned',profile:'Default',interactive:true,
  plan:{entry_url:'https://example.com/'},
});
assert.equal(createdTab,identity==='owned');
