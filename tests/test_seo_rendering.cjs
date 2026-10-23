/* Actual rendered book script, delayed statistics and isolated event stubs. */
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const rendered = require('./rendered_template.cjs');
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const html = rendered('book.html', '/books/book20');
  const script = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].find(x => x[2].includes('const BOOKS='))[2];
  const nodes = new Map(), events = [], calls = [];
  const values = new Map([['taboBooksCart',JSON.stringify([{type:'book',id:'book19'}])],['taboCountry','KW']]);
  const node = id => {
    if (!nodes.has(id)) nodes.set(id,{textContent:'',className:'',hidden:false,setAttribute(){},removeAttribute(name){delete this[name]}});
    return nodes.get(id);
  };
  let releaseStats;
  const stats = new Promise(resolve => {releaseStats=resolve;});
  const c = {
    URLSearchParams, navigator:{language:'ar-KW'},
    location:{pathname:'/books/book20',search:'?utm_source=facebook&utm_campaign=sigma&fbclid=synthetic'},
    document:{documentElement:{},getElementById:node,querySelectorAll:()=>[]},
    localStorage:{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)},
    TaboTracking:{track:(name,data)=>events.push({name,data})},
    fetch:async url=>{
      calls.push(url);
      if (url==='/api/book-stats') return stats;
      const country=new URL(url,'https://synthetic.invalid').searchParams.get('country');
      return {ok:true,json:async()=>({country,currency:country==='EG'?'EGP':'USD',prices:{1:country==='EG'?199:9.99},compareAtPrice:country==='EG'?299:14.99,countries:[]})};
    }
  };
  c.window=c;vm.createContext(c);vm.runInContext(script,c);
  assert.equal(node('title').textContent,'السيجما: الذكر الملكي');
  assert.match(node('about').textContent,/الفحولة/);
  assert.equal(node('addBtn').disabled,true);
  assert.match(node('price').textContent,/تحميل السعر/);
  await flush();await flush();
  assert.equal(node('addBtn').disabled,false);
  assert.match(node('price').textContent,/15\.90/);
  assert.equal(node('pages').textContent,245);
  assert.equal(node('previewLink').hidden,true);
  assert.equal(events.filter(e=>e.name==='ViewContent').length,1);
  assert(calls.includes('/api/book-stats'));
  node('addBtn').onclick();node('addBtn').onclick();
  assert.equal(events.filter(e=>e.name==='AddToCart').length,1);
  assert.deepEqual(JSON.parse(values.get('taboBooksCart')).map(x=>x.id),['book19','book20']);
  await node('country').onchange({target:{value:'EG'}});
  assert.match(node('price').textContent,/500/);
  assert.equal(events.filter(e=>e.name==='ViewContent').length,1);
  releaseStats({ok:true,json:async()=>({book20:{rating:null,reviews:0,downloads:0,pageCount:245}})});
  await flush();await flush();
  assert.equal(events.filter(e=>e.name==='ViewContent').length,1);
  node('languageToggle').onclick();
  assert.equal(c.document.documentElement.lang,'en');
  assert.match(node('title').textContent,/Sigma/);
  assert.equal(c.location.search,'?utm_source=facebook&utm_campaign=sigma&fbclid=synthetic');
  assert(!events.some(e=>e.name==='Purchase'));
  // All executable inline scripts are syntax-checked after real SSR, including articles.
  for (const [template,route] of [['index.html','/'],['info.html','/categories/business'],['book.html','/books/book19']]) {
    for (const match of rendered(template,route).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      if (!/type="application\/(ld\+)?json"/.test(match[1])) new vm.Script(match[2]);
    }
  }
  new vm.Script(fs.readFileSync('seo.js','utf8'));
  // A configuration/statistics refresh must not replace a focused related
  // link or collapse a FAQ that the reader has already opened.
  const copies={ar:{title:'عربي',description:'نبذة',breadcrumb:'ar',extra:'Arabic FAQ',body:'Arabic body'},en:{title:'English',description:'Description',breadcrumb:'en',extra:'English FAQ',body:'English body'}};
  const faq={expanded:true,_html:'Arabic FAQ'};
  Object.defineProperty(faq,'innerHTML',{get(){return this._html},set(value){this._html=value;this.expanded=false;}});
  const editorial={document:{documentElement:{lang:'ar'},getElementById:id=>id==='seo-data'?{textContent:JSON.stringify(copies)}:id==='seo-extra'?faq:{innerHTML:''},querySelector:()=>({setAttribute(){}}),querySelectorAll:()=>[]}};
  editorial.window=editorial;vm.createContext(editorial);vm.runInContext(fs.readFileSync('seo.js','utf8'),editorial);
  editorial.document.title='stale';editorial.TaboSEO.render('ar');assert.equal(faq.expanded,true);assert.equal(editorial.document.title,'عربي');
  editorial.TaboSEO.render('en');assert.equal(faq.innerHTML,'English FAQ');
  faq.expanded=true;editorial.document.title='stale';editorial.TaboSEO.render('en');assert.equal(faq.expanded,true);assert.equal(editorial.document.title,'English');
  console.log('SEO rendering: core content before config/stats; exact fixed prices, preserved cart/query/language, single ViewContent/AddToCart and zero Purchase events passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
