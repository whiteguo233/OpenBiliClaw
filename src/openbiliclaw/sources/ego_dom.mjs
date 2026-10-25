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
  const hrefPrefix = e => {try {
    const raw=(e.getAttribute('href')||'').trim(), u=new URL(raw,location.href);
    if(!raw || !['http:','https:'].includes(u.protocol) || u.username || u.password) return '';
    return raw.split(/[?#]/,1)[0].slice(0,300);
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
    url_redactions:['userinfo','query','fragment'],
    title:document.title.slice(0,200),main_selector:selector(main),
    elements:rows,login_wall:[...document.querySelectorAll('input[type=password]')].some(visible)};
}`;

export const COLLECT_JS = String.raw`({step,limit}) => {
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
  const items=[...document.querySelectorAll(step.item_selector)].slice(0,limit).map(node=>{
    const a=field(node,step.link_selector);
    const t=step.published_selector?field(node,step.published_selector):null;
    return {title:text(node,step.title_selector||step.link_selector).slice(0,500),url:a?.href||'',
      author_name:step.author_selector?text(node,step.author_selector).slice(0,200):'',
      published_at:(t?.getAttribute(step.published_attribute)||t?.textContent||'').slice(0,100),
      description:step.description_selector?text(node,step.description_selector).slice(0,2000):''};
  });
  const next=step.next_selector?document.querySelector(step.next_selector):null;
  const empty=step.empty_selector?visible(document.querySelector(step.empty_selector)):false;
  return {items,empty,
    next:next?.href||''};
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
