/* Independent synthetic DOM/transport; no owner login or real mailing. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{execFileSync}=require('node:child_process');
const base=JSON.parse(execFileSync('python3',['-c',`import json,seo,catalog,localization
print(json.dumps({'books':seo.BOOKS,'offers':catalog.OFFERS,'prices':{'EGP':{str(k):v for k,v in catalog.PRICES.items()},'USD':localization.USD_PRICES},'lockedBooks':list(catalog.FIXED_PRICES)}))`],{encoding:'utf8'}));
const flush=()=>new Promise(r=>setImmediate(r));let registry={};
class Node{constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.handlers={};this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.checked=false;this._text=''}set id(x){this._id=x;registry[x]=this}get id(){return this._id}set textContent(x){this._text=String(x)}get textContent(){return this._text}set innerHTML(_){throw Error('No untrusted HTML')}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}addEventListener(e,fn){this.handlers[e]=fn}setAttribute(){}scrollIntoView(){}querySelectorAll(selector){const result=[];function walk(n){for(const c of n.children){if(c.tagName==='INPUT'&&(selector==='input'||selector==='input:checked'&&c.checked))result.push(c);walk(c)}}walk(this);return result}}
(async()=>{
 registry={};for(const m of fs.readFileSync('admin-dashboard.html','utf8').matchAll(/id="([^"]+)"/g)){const n=new Node;n.id=m[1]}
 const nodes=registry,calls=[],state={base,published:{},draft:{},revision:0,draftRevision:0};
 const c={document:{getElementById:id=>nodes[id],createElement:tag=>new Node(tag)},JSON,Object,Array,Number,Intl,Date,confirm:()=>true,
 fetch:async(url,opts)=>{calls.push({url,opts});let data={};const body=opts.body?JSON.parse(opts.body):null;
 if(url.endsWith('/catalog'))data=JSON.parse(JSON.stringify(state));
 else if(url.endsWith('/catalog/draft')){state.draft=body.document;state.draftRevision++;data={ok:true,draftRevision:state.draftRevision}}
 else if(url.endsWith('/catalog/publish')){state.published=state.draft;state.revision++;data={ok:true,revision:state.revision}}
 else if(url.endsWith('/recovery'))data={enabled:true,counts:[{state:'pending',count:2}],recent:[]};
 else throw Error('Unexpected endpoint');return {ok:true,json:async()=>data}}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('admin-store.js','utf8'),c);
 assert.equal(calls.length,0);c.TaboStoreAdmin.open('catalog');await flush();await flush();
 assert.equal(nodes.editorBook.value,'book1');assert.equal(nodes.catalogPublish.disabled,true);
 nodes['bookEdit-name'].value='<img onerror=attack> synthetic';nodes['bookEdit-name'].handlers.input();
 nodes.bookMoveDown.handlers.click();assert.equal(nodes.catalogPublish.disabled,true);
 await nodes.catalogSave.handlers.click();assert.equal(state.draft.books.book1.name,'<img onerror=attack> synthetic');assert.equal(state.published.books,undefined);
 nodes.catalogPreview.handlers.click();assert.equal(nodes.catalogPublish.disabled,false);
 await nodes.catalogPublish.handlers.click();assert.equal(state.published.books.book1.name,'<img onerror=attack> synthetic');
 assert.equal(state.published.order[1],'book1');
 nodes.editorBook.value='book20';nodes.editorBook.handlers.change();assert.equal(nodes.independentPrice.disabled,true);assert.equal(nodes.bookPriceUSD.disabled,true);
 nodes.editorOffer.value='sigma-couple';nodes.editorOffer.handlers.change();assert.equal(nodes.offerPriceUSD.disabled,true);
 nodes.editorOffer.value='sigma-trilogy';nodes.editorOffer.handlers.change();assert.equal(nodes.offerPriceUSD.disabled,true);assert.equal(nodes.offerPriceEGP.disabled,true);assert.equal(nodes.offerBooks.querySelectorAll('input').length,3);assert(nodes.offerBooks.querySelectorAll('input').every(n=>n.disabled));
 c.TaboStoreAdmin.open('recovery');await flush();await flush();assert(nodes.recoveryState.textContent.includes('مفعّلة'));
 assert(calls.every(x=>x.url.startsWith('/api/admin-dashboard/')&&x.opts.credentials==='same-origin'&&x.opts.cache==='no-store'));
 assert(!JSON.stringify(calls).includes('SUPABASE_SECRET_KEY'));
 function recovery(data){registry={};for(const id of ['recovery-data','recoveryAction','recoveryTitle','recoveryDescription','recoveryBookList','recoveryResult']){const n=new Node;n.id=id}const n=registry;n['recovery-data'].textContent=JSON.stringify(data);const sent=[],saved=[],redirects=[];const r={document:{getElementById:id=>n[id],createElement:tag=>new Node(tag)},JSON,Set,localStorage:{setItem:(...x)=>saved.push(x)},location:{assign:x=>redirects.push(x)},fetch:async(...x)=>{sent.push(x);return{ok:true}}};vm.createContext(r);vm.runInContext(fs.readFileSync('checkout-recovery.js','utf8'),r);return {n,sent,saved,redirects}}
 const cart=recovery({mode:'cart',cart:{items:[{id:'book1'},{id:'book1'},{id:'../secret'}]},names:{book1:'Synthetic'}});cart.n.recoveryAction.handlers.click();assert.equal(cart.redirects[0],'/checkout');assert.deepEqual(JSON.parse(cart.saved[0][1]),[{type:'book',id:'book1'}]);assert.equal(cart.sent.length,0);
 const stop=recovery({mode:'unsubscribe',token:'a'.repeat(64)});assert.equal(stop.sent.length,0);await stop.n.recoveryAction.handlers.click();assert.equal(stop.sent.length,1);assert.equal(stop.sent[0][1].method,'POST');
 const paid=recovery({mode:'cart',cart:{completed:true}});assert(!paid.n.recoveryAction.handlers.click);
 assert(/id="recoveryConsent"/.test(fs.readFileSync('checkout.html','utf8')));assert(!/id="recoveryConsent"[^>]*checked/.test(fs.readFileSync('checkout.html','utf8')));
 console.log('Store admin: private draft/save/preview/publish, reorder IDs, protected Sigma, recovery views and cart-only links passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
