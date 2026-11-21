// Native-method fixtures only. The fixed host runs over a real loopback socket.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [hostPath,ready,auth_token,wrongInstance]=process.argv.slice(2);
let spaces=0;
let createdTab=false;
let closedProbe=false;
const profile='C:\\Users\\Test User\\OpenBiliClaw\\ego-managed\\profile';
globalThis.ego={
  listProfiles:async()=>({profiles:[{id:'Default'}]}),
  createTaskSpace:async()=>({id:++spaces}),
  useTaskSpace:()=>{},
  closeTaskSpace:async()=>{closedProbe=true;},
  createTab:async()=>{createdTab=true;return {targetId:'owned-tab'};},
  getBrowserVersion:async()=>({currentVersion:'0.6.2.7'}),
  sendCDPMessage:raw=>{
    const {id,method}=JSON.parse(raw);
    assert(!['Browser.getWindowForTarget','Browser.setWindowBounds'].includes(method));
    const result={
      'Browser.getBrowserCommandLine':{arguments:[
        '--ego-server-name='+(wrongInstance==='true'?'daily':'obc-test'),
        '--user-data-dir='+profile,'--headless=new','--no-startup-window',
      ]},
      'Target.attachToTarget':{sessionId:'owned-session'},
      'Page.getFrameTree':{frameTree:{frame:{id:'main'}}},
      'Page.captureScreenshot':{data:'fixture-png'},
    }[method] ?? {};
    queueMicrotask(()=>ego.onCDPMessage(JSON.stringify({id,result})));
  },
};
const {start}=await import(pathToFileURL(hostPath));
await start({
  ready,auth_token,headless:true,windows_host:{server:'obc-test',profile},
  task_id:null,task_name:'owned',profile:'Default',interactive:true,
  plan:{entry_url:'https://example.com/'},
});
assert(closedProbe);
assert.equal(createdTab,wrongInstance!=='true');
