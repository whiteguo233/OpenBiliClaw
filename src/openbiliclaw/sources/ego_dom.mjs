// Fixed repository-authored DOM readers; workflow data never supplies JavaScript.

export const SNAPSHOT_JS = String.raw`(context = null) => {
  const visible = e => { if (!e || !e.getClientRects().length) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.visibility!=='collapse'; };
  const classSelector = e => e.tagName.toLowerCase() + [...e.classList]
    .filter(x => /^[a-zA-Z_][\w-]{0,50}$/.test(x)).slice(0,3)
    .map(x => '.' + CSS.escape(x)).join('');
  const selector = e => {
    if (!e) return '';
    if (e.id && e.id.length <= 80) return '#' + CSS.escape(e.id);
    let s = e.tagName.toLowerCase();
    const name = e.getAttribute('name');
    if (name && name.length <= 80) return s + '[name=' + JSON.stringify(name) + ']';
    const cs = [...e.classList].filter(x => /^[a-zA-Z_][\w-]{0,50}$/.test(x)).slice(0,3);
    return s + cs.map(x=>'.'+CSS.escape(x)).join('');
  };
  const path = (e,stop=null) => {
    const parts=[];
    while(e && e!==stop && e!==document.body && parts.length<5) {
      parts.unshift(selector(e));
      if(!stop && e.id && e.id.length<=80) break;
      e=e.parentElement;
    }
    if(stop && e!==stop) return '';
    const result=parts.join(' > ');
    return result.length<=300?result:'';
  };
  const safeUrl = s => {try {const u=new URL(s,location.href);u.username='';u.password='';
    u.search='';u.hash='';return u.href;}catch{return '';}};
  // Page identity is compared before redaction; no query, fragment or userinfo
  // value crosses the observation boundary. A sanitized URL alone cannot
  // distinguish two boards (or SPA routes) sharing the same path.
  const matchesEntry = () => {try {return context?.entry_url ?
    new URL(location.href).href===new URL(context.entry_url).href : null;}catch{return null;}};
  const compareEntry = () => {try {
    if(!context?.entry_url) return null;
    const current=new URL(location.href), entry=new URL(context.entry_url);
    const requestedKeys=[...new Set(entry.searchParams.keys())];
    return {
      origin_matches:current.origin===entry.origin,path_matches:current.pathname===entry.pathname,
      requested_query_matches:requestedKeys.every(key=>
        JSON.stringify(current.searchParams.getAll(key))===JSON.stringify(entry.searchParams.getAll(key))),
      additional_query_parameters:[...current.searchParams.keys()].filter(key=>!entry.searchParams.has(key)).length,
      requested_fragment_matches:!entry.hash || current.hash===entry.hash,
      additional_fragment:!entry.hash && !!current.hash,
    };
  }catch{return null;}};
  const hrefPrefix = e => {try {
    const raw=e.getAttribute('href')||'', u=new URL(raw,location.href);
    if(!raw.trim() || !['http:','https:'].includes(u.protocol) || u.username || u.password) return '';
    const prefix=raw.split(/[?#]/,1)[0];
    return prefix.trim()?prefix.slice(0,300):'';
  }catch{return '';}};
  const text = e => ['INPUT','TEXTAREA','SELECT','FORM'].includes(e.tagName) ? '' :
    (e.innerText||'').trim().slice(0,180);
  const main = document.querySelector('main,[role=main],#main-content,#content,.main-content')
    || document.body;
  const tags='a,button,input,form,article,section,ul,li,p,span.meta,time,h1,h2,h3';
  const controls=[...document.querySelectorAll('input,form,button,textarea,select')]
    .filter(visible).slice(0,20);
  // Repeated result rows can follow a long header or pinned section. Sample
  // across the list instead of letting the first hundred links hide its tail.
  const containers=[...document.querySelectorAll('tr,[role=row],article')].filter(visible).slice(0,500);
  const head=containers.slice(0,4), rest=containers.slice(4), count=Math.min(12,rest.length);
  const samples=[...head,...Array.from({length:count},(_,i)=>rest[Math.round(i*(rest.length-1)/Math.max(1,count-1))])];
  const structured=samples.flatMap(e=>{
    const links=[...e.querySelectorAll('a[href]')].filter(visible);
    return [e,...links.slice(0,2),...links.slice(-2)];
  });
  const content=[...main.querySelectorAll(tags)].filter(visible).slice(0,80);
  const remaining=[...document.querySelectorAll(tags)].filter(visible).slice(0,100);
  const chosen=[...new Set([...controls,...structured,...content,...remaining])].slice(0,100);
  const rows=chosen.map(e=>{
    const ancestors=[];
    for(let a=e.parentElement;a && a!==document.body && ancestors.length<3;a=a.parentElement)
      ancestors.push(selector(a));
    const childLinks=[...e.querySelectorAll('a[href]')].filter(visible).slice(0,3).map(a=>({
      selector:path(a,e),class_selector:classSelector(a),href:safeUrl(a.href),
      href_attribute_prefix:hrefPrefix(a),text:text(a).slice(0,120)
    })).filter(a=>a.selector);
    return {tag:e.tagName.toLowerCase(),selector:selector(e),class_selector:classSelector(e),path_selector:path(e),
      text:text(e),href:e.tagName==='A'?safeUrl(e.href):'',
      href_attribute_prefix:e.tagName==='A'?hrefPrefix(e):'',type:e.getAttribute('type')||'',
      method:e.tagName==='FORM'?(e.method||'get'):'',
      action:e.tagName==='FORM'?safeUrl(e.action):'',
      parent_selector:selector(e.parentElement),parent_path_selector:path(e.parentElement),
      ancestor_selectors:ancestors,
      previous_sibling_selector:selector(e.previousElementSibling),
      next_sibling_selector:selector(e.nextElementSibling),
      child_selectors:[...e.children].slice(0,6).map(selector),child_links:childLinks};
  });
  return {url:safeUrl(location.href),url_matches_entry:matchesEntry(),
    entry_url_comparison:compareEntry(),
    url_redactions:['userinfo','query','fragment'],
    title:document.title.slice(0,200),main_selector:selector(main),
    elements:rows,login_wall:[...document.querySelectorAll('input[type=password]')].some(visible)};
}`;

export const COLLECT_JS = String.raw`({step,limit,capture_evidence=false}) => {
  try {
  const field = (node,sel) => {
    const selector=sel.trim();
    if(selector===':scope') return node;
    return node.querySelector(/^[>+~]/.test(selector)?':scope '+selector:selector);
  };
  const text = (node,sel) => {const e=sel?field(node,sel):node;
    return (e?.innerText||e?.textContent||'').trim();};
  const visible = e => { if (!e || !e.getClientRects().length) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.visibility!=='collapse'; };
  const evidenceNode=e=>({tag:(e?.tagName||'').toLowerCase(),
    id:/^[A-Za-z_][\w-]{0,79}$/.test(e?.id||'')?e.id:'',
    classes:[...(e?.classList||[])].filter(c=>/^[A-Za-z_][\w-]{0,50}$/.test(c)).slice(0,10),
    text:['INPUT','TEXTAREA','SELECT','FORM'].includes(e?.tagName)?'':
      (e?.innerText||e?.textContent||'').trim().slice(0,500)});
  const evidenceLink=e=>{let prefix='';try {
    const raw=e?.getAttribute('href')||'', url=new URL(raw,location.href);
    if(raw.trim() && ['http:','https:'].includes(url.protocol) && !url.username && !url.password) {
      const value=raw.split(/[?#]/,1)[0];prefix=value.trim()?value.slice(0,300):'';
    }
  }catch{} return {...evidenceNode(e),href_attribute_prefix:prefix};};
  const matchedNodes=[...document.querySelectorAll(step.item_selector)], nodes=matchedNodes.slice(0,limit);
  // Also prove the inverse spelling mistake in a zero-row list predicate.
  // Only one terminal :has(simple anchor[href^="/path"]) is supported: every
  // other structural/path constraint survives, including navigation exclusions.
  const itemHrefRepair=()=>{
    if(matchedNodes.length)return null;
    const match=step.item_selector.match(/^([A-Za-z_*#.][A-Za-z0-9_*#.\s>+~-]*):has\((a(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*)\[href\^=(["'])(\/[A-Za-z0-9/_.~-]+)\3\]\)$/);
    if(!match)return null;
    const path=match[4], absolute=location.origin+path;
    if(path.startsWith('//') || new URL(path,document.baseURI).href!==absolute)return null;
    const anchorSelector=match[2]+'[href^='+match[3]+absolute+match[3]+']';
    const corrected=match[1]+':has('+anchorSelector+')';
    const rows=[...document.querySelectorAll(corrected)];
    if(!rows.length || rows.length>100)return null;
    const all=(node,sel)=>node.querySelectorAll(/^[>+~]/.test(sel)?':scope '+sel:sel);
    const valid=rows.every(node=>{
      const anchors=all(node,anchorSelector), links=all(node,step.link_selector),
        titles=all(node,step.title_selector||step.link_selector);
      if(anchors.length!==1 || links.length!==1 || titles.length!==1)return false;
      const anchor=anchors[0];
      return anchor===links[0] && anchor===titles[0] && anchor.tagName==='A' &&
        (anchor.innerText||anchor.textContent||'').trim() &&
        anchor.href.startsWith(absolute) &&
        (anchor.getAttribute('href')||'').split(/[?#]/,1)[0]===absolute;
    });
    return valid?{origin:location.origin}:null;
  };
  // A model can confuse resolved URLs with literal href attributes. Prove a
  // narrow correction against these exact rows; never use it during collection.
  // Query/hash constraints, escaped/compound selectors and mixed rows stay errors.
  const hrefRepairs=()=>['link_selector','title_selector'].filter(key=>{
    const selector=step[key];
    if(!selector || !matchedNodes.length || matchedNodes.length>100 || /[\\(),]/.test(selector))return false;
    const matches=[...selector.matchAll(/\[href\^=(["'])(https?:\/\/[^"'\\\s?#]+)\1\]/g)];
    if(matches.length!==1)return false;
    const match=matches[0], expected=match[2], url=new URL(expected);
    if(url.origin!==location.origin || url.username || url.password || url.search || url.hash ||
      url.href!==expected || !/^\/[A-Za-z0-9/_%.~-]+$/.test(url.pathname))return false;
    const broad=selector.replace(match[0],'[href]');
    const corrected=selector.replace(match[0],'[href^='+match[1]+url.pathname+match[1]+']');
    return matchedNodes.every(node=>{
      if(field(node,selector))return false; // Do not break already matching/mixed rows.
      const candidates=[...node.querySelectorAll(/^[>+~]/.test(broad)?':scope '+broad:broad)];
      if(candidates.length!==1)return false;
      const candidate=candidates[0];
      if(candidate.tagName!=='A' || !candidate.href.startsWith(expected) ||
        (candidate.getAttribute('href')||'').split(/[?#]/,1)[0]!==url.pathname)return false;
      const verified=[...node.querySelectorAll(/^[>+~]/.test(corrected)?':scope '+corrected:corrected)];
      return verified.length===1 && verified[0]===candidate;
    });
  });
  const items=nodes.map(node=>{
    const a=field(node,step.link_selector);
    const t=step.published_selector?field(node,step.published_selector):null;
    return {title:text(node,step.title_selector||step.link_selector).slice(0,500),url:a?.href||'',
      author_name:step.author_selector?text(node,step.author_selector).slice(0,200):'',
      published_at:(t?.getAttribute(step.published_attribute)||t?.textContent||'').slice(0,100),
      description:step.description_selector?text(node,step.description_selector).slice(0,2000):'',
      ...(capture_evidence?{evidence:{row:evidenceNode(node),link:evidenceLink(a)}}:{})};
  });
  const next=step.next_selector?document.querySelector(step.next_selector):null;
  const empty=step.empty_selector?visible(document.querySelector(step.empty_selector)):false;
  let repairs=[];
  let itemRepair=null;
  let collectionContext=null;
  try {if(items.some(item=>!item.url || !item.title))repairs=hrefRepairs();}catch{}
  try {if(!items.length && !empty)itemRepair=itemHrefRepair();}catch{}
  if(capture_evidence) {
    const base=step.item_selector.split(':has(',1)[0], selected=new Set(matchedNodes);
    if(/^[A-Za-z#.][A-Za-z0-9_#.\s>+~-]*$/.test(base)) {
      const envelope=[...document.querySelectorAll(base)], excluded=envelope.filter(e=>!selected.has(e));
      collectionContext={base_selector:base,base_match_count:envelope.length,
        item_match_count:matchedNodes.length,excluded_row_count:excluded.length,
        excluded_rows:excluded.slice(0,10).map(e=>({row:evidenceNode(e),
          links:[...e.querySelectorAll('a[href]')].slice(0,3).map(evidenceLink)}))};
    }
  }
  return {items,empty,next:next?.href||'',...(repairs.length?{href_selector_repairs:repairs}:{}),
    ...(itemRepair?{item_href_selector_repair:itemRepair}:{}),
    ...(capture_evidence?{collection_match_count:matchedNodes.length,collection_context:collectionContext}:{})};
  } catch(error) {
    // CSS parser failures are actionable plan evidence, not transport failures.
    // Never expose the exception body, which can contain page/selector data.
    if(error?.name==='SyntaxError')return {items:[],empty:false,next:'',
      collection_diagnostics:{error:'invalid_css_selector'}};
    throw error;
  }
}`;

export const FORM_JS = String.raw`selector => {
  const matches=document.querySelectorAll(selector);
  if(matches.length!==1 || matches[0].tagName!=='FORM') return null;
  const f=matches[0];
  if((f.method||'get').toLowerCase()!=='get') return null;
  if(f.querySelector('input[type=password]')) return null;
  const fields=[...new FormData(f).entries()].filter(([k,v])=>typeof v==='string');
  return {action:f.action,fields};
}`;

export const SEARCH_INPUT_JS = String.raw`selector => {
  const es=document.querySelectorAll(selector); if(es.length!==1) return false;
  const e=es[0]; return e.tagName==='INPUT' && ['search','text',''].includes(e.type)
    && !/password|token|secret|credential|otp|verification/i.test(e.name+' '+e.id);
}`;
