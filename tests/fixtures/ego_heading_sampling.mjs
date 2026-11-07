// A div-based news list after a long sidebar must remain visible to authoring.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const {SNAPSHOT_JS}=await import(pathToFileURL(process.argv[2]));
const element=(tag,id='')=>({tagName:tag,id,classList:[],children:[],innerText:'',
  getClientRects:()=>[{}],getBoundingClientRect:()=>({width:20,height:10}),
  getAttribute:()=>null,querySelectorAll:()=>[]});
const body=element('BODY');
const headings=Array.from({length:40},(_,i)=>{
  const card=element('DIV','story_'+i), heading=element('H2','title_'+i), a=element('A','link_'+i);
  card.parentElement=body;heading.parentElement=card;a.parentElement=heading;
  card.children=[heading];heading.children=[a];a.innerText=heading.innerText='News '+i;
  a.href='https://news.example/story?id='+i+'&token=private';
  a.getAttribute=k=>k==='href'?'/story?id='+i+'&token=private':null;
  heading.querySelectorAll=s=>s==='a[href]'?[a]:[];
  return heading;
});
const sidebar=Array.from({length:160},(_,i)=>{
  const a=element('A','side_'+i);a.parentElement=body;return a;
});
const all=[...sidebar,...headings.flatMap(e=>[e,...e.children])];
body.querySelectorAll=()=>all;
let rows=[];
const document={body,title:'News',querySelector:()=>body,
  querySelectorAll:s=>s==='h1,h2,h3,h4,h5,h6'?headings:
    s==='tr,[role=row],article'?rows:
    s==='input[type=password]'||s==='input,form,button,textarea,select'?[]:all};
const observe=()=>vm.runInNewContext('('+SNAPSHOT_JS+')()',
  {document,URL,location:{href:'https://news.example/'},CSS:{escape:x=>x},
    getComputedStyle:()=>({visibility:'visible'})});
let result=observe();
assert(result.elements.some(e=>e.selector==='#link_0'),'first news must survive the sidebar budget');
assert(result.elements.some(e=>e.selector==='#link_39'),'sample the news tail as well');
assert(result.elements.some(e=>e.selector==='#title_0' && e.child_links.length===1));
assert(!JSON.stringify(result).includes('private'),'query secrets remain redacted');
assert(result.elements.length<=100);
// Preserve both evidence families when a table precedes the heading-based list.
rows=Array.from({length:40},(_,i)=>{
  const r=element('TR','row_'+i),a=element('A','topic_'+i);r.parentElement=body;a.parentElement=r;
  a.innerText='Topic '+i;a.href='https://news.example/topic/'+i;r.children=[a];
  r.querySelectorAll=s=>s==='a[href]'?[a]:[];return r;
});
result=observe();
for(const id of ['#link_0','#link_39','#topic_0','#topic_39'])
  assert(result.elements.some(e=>e.selector===id),'both lists retain head and tail: '+id);
assert(result.elements.length<=100);
console.log('heading sampling passed');
