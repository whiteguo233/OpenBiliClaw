// Zero-row list predicate with absolute topic links and excluded navigation rows.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';

const {COLLECT_JS}=await import(pathToFileURL(process.argv[2]));
const mode=process.argv[3], origin='https://forum.example';
const path='/read.php', absolute=origin+path;
const before='#topics > tbody > tr.topic:has(a.topic[href^="'+path+'"])';
const after=before.replace('"'+path+'"','"'+absolute+'"');
const anchorSelector='a.topic[href^="'+absolute+'"]';
const field='td.content > '+anchorSelector;
const anchor={tagName:'A',href:absolute+'?tid=123',innerText:'A public topic',
  getAttribute:()=>absolute+'?tid=123'};
const other={...anchor};
if(mode==='cross_origin')anchor.href='https://other.example/read.php?tid=123';
if(mode==='raw_relative')anchor.getAttribute=()=>'/read.php?tid=123';
if(mode==='blank')anchor.innerText='';
const row={querySelector:selector=>row.querySelectorAll(selector)[0]??null,
  querySelectorAll:selector=>selector===anchorSelector?
    (mode==='ambiguous'?[anchor,other]:[anchor]):
    selector===field?(mode==='wrong_field'?[other]:mode==='missing_field'?[]:[anchor]):[]};
const step={item_selector:before,link_selector:field,title_selector:field};
if(mode==='query')step.item_selector=before.replace('read.php"','read.php?scope=safe"');
if(mode==='fragment')step.item_selector=before.replace('read.php"','read.php#safe"');
if(mode==='escaping')step.item_selector=before.replace('a.topic','a.to\\pic');
if(mode==='selector_list')step.item_selector=before+',tr.other';
if(mode==='nested')step.item_selector=before.replace(':has(a.topic',':not(a.other):has(a.topic');
if(mode==='case_flag')step.item_selector=before.replace('"]','" i]');
if(mode==='parent_relative')step.item_selector=before.replace('/read.php','../read.php');
const original=JSON.stringify(step);
const document={baseURI:mode==='foreign_base'?'https://other.example/topics':origin+'/topics',
  querySelectorAll:selector=>{
    if(selector===step.item_selector)return mode==='mixed'?[row]:[];
    assert.equal(selector,after,'do not remove the path or structural filter to include navigation');
    if(mode==='empty')return [];
    if(mode==='over_limit')return Array(101).fill(row);
    if(mode==='ambiguous_beyond_budget')return [row,{...row,querySelectorAll:()=>[anchor,other]}];
    return [row];
  }};
const collect=vm.runInNewContext('('+COLLECT_JS+')',{document,URL,location:{origin,href:origin+'/topics'}});
const result=JSON.parse(JSON.stringify(collect({step,limit:1})));
assert.equal(JSON.stringify(step),original);
if(mode!=='mixed')assert.deepEqual(result.items,[],'proof does not silently collect repaired rows');
assert.deepEqual(result.item_href_selector_repair??null,mode==='absolute'?{origin}:null);
console.log(JSON.stringify({mode,repair:result.item_href_selector_repair??null}));
