import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const {COLLECT_JS}=await import(pathToFileURL(process.argv[2]));
const mode=process.argv[3];
const document={querySelectorAll:()=>{
  if(mode==='syntax')throw new SyntaxError('Untrusted selector text is not returned');
  throw new Error('Transport or page runtime problem');
}};
const collect=vm.runInNewContext('('+COLLECT_JS+')',{document});
if(mode==='syntax')assert.deepEqual(JSON.parse(JSON.stringify(collect({step:{item_selector:'x'},limit:3}))),{
  items:[],empty:false,next:'',collection_diagnostics:{error:'invalid_css_selector'},
});
else assert.throws(()=>collect({step:{item_selector:'x'},limit:3}),/Transport or page runtime/);
console.log('ok');
