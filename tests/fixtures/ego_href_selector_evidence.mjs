// Real collection reader, with row-scoped DOM results for a mixed-href page.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';

const {COLLECT_JS}=await import(pathToFileURL(process.argv[2]));
const mode=process.argv[3];
const origin='https://forum.example';
const absolute=(mode==='cross_origin'?'https://other.example':origin)+'/read.php';
let before='td.content > a.topic[href^="'+absolute+'"]';
if(mode==='query')before=before.replace('read.php"','read.php?topic=safe"');
if(mode==='fragment')before=before.replace('read.php"','read.php#safe"');
if(mode==='escaping')before=before.replace('a.topic','a.to\\pic');
if(mode==='case_flag')before=before.replace('"]','" i]');
if(mode==='selector_list')before+=', a.other';
const after='td.content > a.topic[href^="/read.php"]';
const broad='td.content > a.topic[href]';
const anchor={tagName:'A',href:absolute+'?tid=123',innerText:'A public topic',
  getAttribute:name=>name==='href'?
    (mode==='parent_relative'?'../read.php?tid=123':
     mode==='whitespace'?' /read.php?tid=123':'/read.php?tid=123'):null};
if(mode==='wrong_destination')anchor.href=origin+'/other.php?tid=123';
const other={...anchor,innerText:'Another topic'};
const row={querySelector:selector=>row.querySelectorAll(selector)[0]??null,
  querySelectorAll:selector=>selector===broad?
    (mode==='ambiguous'?[anchor,other]:[anchor]):
    selector===after?(mode==='changed_match'?[other]:[anchor]):[]};
const absoluteRow={querySelector:selector=>selector===before?other:null,
  querySelectorAll:selector=>selector===before||selector===broad?[other]:[]};
const step={item_selector:'tr.topic:has(a.topic[href^="/read.php"])',
  link_selector:before,title_selector:before};
const original=JSON.stringify(step);
const document={querySelectorAll:selector=>{assert.equal(selector,step.item_selector);
  return mode==='empty'?[]:['mixed','mixed_beyond_budget'].includes(mode)?[row,absoluteRow]:[row];}};
const collect=vm.runInNewContext('('+COLLECT_JS+')',{document,URL,location:{origin,href:origin+'/topics?board=7'}});
const result=JSON.parse(JSON.stringify(collect({step,limit:mode==='mixed_beyond_budget'?1:3})));
assert.equal(JSON.stringify(step),original,'collect must not rewrite a saved plan');
if(mode!=='empty')assert.equal(result.items[0].url,'','failed items must not be silently repaired');
if(mode==='mixed')assert.equal(result.items[1].url,other.href,'retain originally matching rows');
const repairs=result.href_selector_repairs??[];
assert.deepEqual(repairs,mode==='relative'?['link_selector','title_selector']:[]);
console.log(JSON.stringify({mode,repairs}));
