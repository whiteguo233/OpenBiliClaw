// Execute the actual reader against a minimal Element contract. This fixture
// models querySelector's invalid-leading-combinator and descendant-only rules;
// real native CSS parsing remains covered by the separate Ego live acceptance.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';

const {COLLECT_JS} = await import(pathToFileURL(process.argv[2]).href);
const mode = process.argv[3];
const leaf = (text, extra = {}) => ({textContent:text, ...extra});
const link = leaf('A public book', {href:'https://books.example.com/book/1',tagName:'A',classList:['book-layout'],
  getAttribute:name=>name==='href'?'/book/1?token=redacted':null});
const title = leaf('A distinct title');
const author = leaf('A real author');
const description = leaf('A public synopsis');
const published = leaf('22 October 2026', {
  getAttribute: name => name === 'datetime' ? '2026-10-22' : null,
});
const descendants = new Map([
  ['a.book-layout', link], ['h2', title], ['.author', author],
  ['time', published], ['.description', description],
]);
const queries = [];
const node = {
  ...link,
  tagName:'LI', id:'row-one', classList:['topic'],
  querySelector(selector) {
    queries.push(selector);
    if (/^[>+~]/.test(selector)) throw new SyntaxError('Invalid selector');
    // Element.querySelector searches descendants; :scope alone is not self.
    if (selector === ':scope') return null;
    return descendants.get(selector.replace(/^:scope > /, '')) ?? null;
  },
};
const relative = mode === 'relative';
const select = value => relative ? '> ' + value : value;
const step = {
  item_selector: 'li:has(> a.book-layout)',
  link_selector: select('a.book-layout'),
  title_selector: select('h2'),
  author_selector: select('.author'),
  published_selector: select('time'),
  published_attribute: 'datetime',
  description_selector: select('.description'),
};
if (mode === 'self') {
  step.link_selector = ' :scope ';
  step.title_selector = '';
}
if (mode === 'explicit_scope') {
  for (const key of ['link_selector', 'title_selector', 'author_selector',
    'published_selector', 'description_selector']) step[key] = ':scope > ' + step[key];
}
const original = JSON.stringify(step);
const document = {
  querySelectorAll(selector) {
    assert.ok(['li:has(> a.book-layout)','li'].includes(selector));
    return [node];
  },
};
const collect = vm.runInNewContext('(' + COLLECT_JS + ')', {document,URL,location:{href:'https://books.example.com/'}});
const result = JSON.parse(JSON.stringify(collect({step, limit:1,capture_evidence:mode==='evidence'})));
assert.equal(JSON.stringify(step), original, 'the workflow plan must not be rewritten');
const expected = {
  items: [{title:mode === 'self' ? 'A public book' : 'A distinct title',
    url:link.href, author_name:'A real author', published_at:'2026-10-22',
    description:'A public synopsis'}],
  empty:false, next:'',
};
if(mode==='evidence') {
  expected.items[0].evidence={row:{tag:'li',id:'row-one',classes:['topic'],text:'A public book'},
    link:{tag:'a',id:'',classes:['book-layout'],text:'A public book',href_attribute_prefix:'/book/1'}};
  expected.collection_match_count=1;
  expected.collection_context={base_selector:'li',base_match_count:1,item_match_count:1,
    excluded_row_count:0,excluded_rows:[]};
}
assert.deepEqual(result,expected);
assert.ok(queries.every(selector => !/^[>+~]/.test(selector)));
if (mode === 'self') assert.ok(!queries.includes(':scope'));
console.log(JSON.stringify({mode, count:result.items.length, unchanged_plan:true}));
