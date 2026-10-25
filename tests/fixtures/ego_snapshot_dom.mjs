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
link.href='https://forum.example/read?tid=123&token=secret';link.innerText='A topic';
link.getAttribute=name=>name==='href'?'/read?tid=123&token=secret':null;
row.querySelectorAll=()=>[link];body.querySelectorAll=()=>[row,link];
const document={body,title:'Forum',querySelector:()=>body,
  querySelectorAll:s=>s==='input[type=password]'||s==='input,form,button,textarea,select'?[]:[row,link]};
const observe=(current,entry,link_patterns=[])=>vm.runInNewContext('('+SNAPSHOT_JS+')('+JSON.stringify({entry_url:entry,link_patterns})+')',
  {document,URL,location:{href:current},CSS:{escape:x=>x},getComputedStyle:()=>({visibility:'visible'})});
const entry='https://forum.example/topics?section=7&token=private#results';
const observation=observe(entry,entry);
const observed=observation.elements.find(x=>x.selector==='#item_1');
assert.equal(observed.class_selector,'a.topic.highlight','IDs must not hide reusable classes');
assert.equal(observation.elements[0].child_links[0].class_selector,'a.topic.highlight');
assert.equal(observed.href,'https://forum.example/read','do not reveal query credentials');
assert.equal(observed.href_attribute_prefix,'/read','retain relative href evidence for CSS attribute selectors');
assert.equal(observation.elements[0].child_links[0].href_attribute_prefix,'/read');
assert.equal(observation.url,'https://forum.example/topics');
assert.equal(observation.url_matches_entry,true,'query redaction must not suggest a different page');
assert.equal(JSON.stringify(observation.url_redactions),JSON.stringify(['userinfo','query','fragment']));
const extra=observe('https://forum.example/topics?section=7&token=private&rand=secret#results',entry);
assert.equal(extra.url_matches_entry,false,'preserve exact comparison even when the website adds a nonce');
assert.equal(JSON.stringify(extra.entry_url_comparison),JSON.stringify({
  origin_matches:true,path_matches:true,requested_query_matches:true,
  additional_query_parameters:1,requested_fragment_matches:true,additional_fragment:false,
}),'distinguish a website-added query parameter from changing the requested section');
for(const current of [
  'https://forum.example/topics?section=8&token=private#results',
  'https://forum.example/topics?token=private#results',
  'https://forum.example/topics?section=7&section=8&token=private#results',
]) assert.equal(observe(current,entry).entry_url_comparison.requested_query_matches,false);
assert.equal(observe('https://other.example/topics?section=7&token=private#results',entry)
  .entry_url_comparison.origin_matches,false);
assert.equal(observe('https://forum.example/elsewhere?section=7&token=private#results',entry)
  .entry_url_comparison.path_matches,false);
assert.equal(observe('https://forum.example/topics?section=7&token=private#other',entry)
  .entry_url_comparison.requested_fragment_matches,false);
assert.equal(observe('https://forum.example/topics?section=7#results','https://forum.example/topics?section=7')
  .entry_url_comparison.additional_fragment,true);
assert.equal(JSON.stringify(extra).includes('secret'),false);
assert.equal(JSON.stringify(extra).includes('private'),false);
assert.equal(observe('https://forum.example/topics?section=8',entry).url_matches_entry,false,
  'a genuine change of board must remain distinguishable');
assert.equal(observe('https://forum.example/topics?section=7&token=private#other',entry).url_matches_entry,false,
  'SPA fragments also affect exact page identity');
assert.equal(JSON.stringify(observation).includes('secret'),false);
assert.equal(JSON.stringify(observation).includes('private'),false);
const patterns=[{path:'/read',query_key:'tid'},{path:'/read',query_key:'missing'},
  {path:'/elsewhere',query_key:'tid'}];
const checked=observe(entry,entry,patterns);
const checks=checked.elements.find(x=>x.selector==='#item_1').href_query_checks;
assert.equal(JSON.stringify(checked.requested_link_patterns),JSON.stringify(patterns));
assert.deepEqual(JSON.parse(JSON.stringify(checks[0])),{
  pattern_index:0,path_matches:true,query_key_present:true,
  raw_key_after_question:true,raw_key_after_ampersand:false,
});
assert.equal(checks[1].query_key_present,false);
assert.equal(checks[2].path_matches,false);
assert.equal(checked.elements[0].child_links[0].href_query_checks[0].query_key_present,true);
assert.equal(JSON.stringify(checked).includes('secret'),false);
assert.equal(JSON.stringify(checked).includes('token'),false,'never enumerate other page query keys');
for(const [raw,prefix] of [
  ['https://forum.example/read?token=secret','https://forum.example/read'],
  ['//forum.example/read?token=secret','//forum.example/read'],
  ['../read?token=secret','../read'],
  [' /read?token=secret',' /read'],[' /read ',' /read '],
  ['?token=secret',''],['#private',''],[' ?token=secret',''],[' #private',''],
  ['javascript:secret()',''],
  ['https://person:password@forum.example/read?token=secret',''],
]) {
  link.getAttribute=name=>name==='href'?raw:null;
  const seen=observe(entry,entry).elements.find(x=>x.selector==='#item_1');
  assert.equal(seen.href_attribute_prefix,prefix,'only publish an observed, credential-free attribute prefix');
}
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
