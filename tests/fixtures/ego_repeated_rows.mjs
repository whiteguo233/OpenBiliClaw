// Reproduce topic-specific ancestor classes without a live website dependency.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const {SNAPSHOT_JS}=await import(pathToFileURL(process.argv[2]));
const element=(tag,classes=[],id='')=>({tagName:tag,classList:classes,id,children:[],
  getClientRects:()=>[{}],getBoundingClientRect:()=>({width:20,height:10}),
  getAttribute:()=>null,querySelectorAll:()=>[],innerText:''});
const add=(parent,child)=>{parent.children.push(child);child.parentElement=parent;return child;};
const body=element('BODY'),list=add(body,element('DIV',[],'TopicsHot'));
const rows=[101,202,303].map(id=>{
  const cell=add(list,element('DIV',['cell','hot_t_'+id]));
  const table=add(cell,element('TABLE')),tbody=add(table,element('TBODY'));
  const row=add(tbody,element('TR'));
  const a=add(row,element('A',['topic']));a.innerText='Topic '+id;
  a.href='https://forum.example/t/'+id;a.getAttribute=k=>k==='href'?'/t/'+id:null;
  row.querySelectorAll=s=>s==='a[href]'?[a]:[];
  return row;
});
const hint='#TopicsHot > div.cell > table > tbody > tr';
let matches=rows;
const document={body,title:'Forum',querySelector:()=>body,
  querySelectorAll:s=>s===hint?matches:s==='tr,[role=row],article'?rows:[]};
const observe=()=>vm.runInNewContext('('+SNAPSHOT_JS+')()',
  {document,URL,location:{href:'https://forum.example/'},CSS:{escape:x=>x},
    getComputedStyle:()=>({visibility:'visible'})});
let seen=observe().elements.filter(e=>e.tag==='tr');
assert.equal(seen.length,3);
assert(seen[0].path_selector.includes('.hot_t_101'),'retain exact raw evidence');
assert.equal(seen[0].repeated_path_selector,hint,'list evidence must not freeze one topic ID');
assert.equal(seen[0].repeated_match_count,3);
assert(seen.every(e=>e.repeated_path_selector===hint));
// One surviving item is insufficient evidence of a reusable cohort.
matches=rows.slice(0,1);
assert.equal(observe().elements[0].repeated_path_selector,'');
// A selector matching another container must not be suggested as this list.
matches=[...rows,element('TR')];
assert.equal(observe().elements[0].repeated_path_selector,'');
// Excessive matches do not enlarge the existing observation budget.
matches=Array.from({length:501},()=>rows[0]);
assert.equal(observe().elements[0].repeated_path_selector,'');
const query=document.querySelectorAll;
document.querySelectorAll=s=>{if(s===hint)throw new SyntaxError('invalid selector');return query(s);};
assert.equal(observe().elements[0].repeated_path_selector,'','an invalid optional hint must not fail observation');
console.log('repeated row evidence passed');
