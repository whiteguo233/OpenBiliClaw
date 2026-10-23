// Exercise the shipped observation reader with unique IDs on repeated links.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const {SNAPSHOT_JS}=await import(pathToFileURL(process.argv[2]));
const element=(tag,classes=[],id='')=>({tagName:tag,classList:classes,id,children:[],
  getClientRects:()=>[{}],getBoundingClientRect:()=>({width:20,height:10}),
  getAttribute:()=>null,querySelectorAll:()=>[],innerText:''});
const body=element('BODY'),row=element('TR',['result-row']),link=element('A',['topic','highlight'],'item_1');
row.parentElement=body;link.parentElement=row;row.children=[link];
link.href='https://forum.example/read?token=secret';link.innerText='A topic';
row.querySelectorAll=()=>[link];body.querySelectorAll=()=>[row,link];
const document={body,title:'Forum',querySelector:()=>body,
  querySelectorAll:s=>s==='input[type=password]'||s==='input,form,button,textarea,select'?[]:[row,link]};
const observation=vm.runInNewContext('('+SNAPSHOT_JS+')()',
  {document,URL,location:{href:'https://forum.example/'},CSS:{escape:x=>x},getComputedStyle:()=>({visibility:'visible'})});
const observed=observation.elements.find(x=>x.selector==='#item_1');
assert.equal(observed.class_selector,'a.topic.highlight','IDs must not hide reusable classes');
assert.equal(observation.elements[0].child_links[0].class_selector,'a.topic.highlight');
assert.equal(observed.href,'https://forum.example/read','do not reveal query credentials');
assert.equal(observed.parent_selector,'tr.result-row');
// A long header must not consume the bounded observation before list rows.
const rows=Array.from({length:40},(_,i)=>{
  const r=element('TR',['result-row'], 'row_'+i);r.parentElement=body;
  const a=element('A',['topic'],'topic_'+i);a.parentElement=r;a.innerText='Topic '+i;
  a.href='https://forum.example/read?token=secret';r.children=[a];r.querySelectorAll=()=>[a];return r;
});
const header=Array.from({length:150},(_,i)=>{const a=element('A',[],'nav_'+i);a.parentElement=body;return a;});
const layout=element('TR');layout.parentElement=body;
const all=[...header,layout,...rows.flatMap(r=>[r,...r.children])];
body.querySelectorAll=()=>all;
document.querySelectorAll=s=>s==='tr,[role=row],article'?[layout,...rows]:
  s==='input[type=password]'||s==='input,form,button,textarea,select'?[]:all;
const long=vm.runInNewContext('('+SNAPSHOT_JS+')()',
  {document,URL,location:{href:'https://forum.example/'},CSS:{escape:x=>x},getComputedStyle:()=>({visibility:'visible'})});
assert(long.elements.some(x=>x.selector==='#topic_39'),'observe topics after a long header');
assert(long.elements.some(x=>x.selector==='#topic_0'));
assert(long.elements.length<=100,'retain the observation bound');
console.log('snapshot classes preserved');
