// All Google transport is stubbed. No events leave this test process.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ga4=fs.readFileSync('ga4-tracking.js','utf8'),ads=fs.readFileSync('google-ads-tracking.js','utf8');
const flush=()=>new Promise(r=>setImmediate(r));
function runtime({url='https://tabobooks.com/books/book1?utm_source=google&gclid=test&email=private@example.invalid#secret',id='G-SYNTHETIC1',block=false,fail=false,storage=new Map(),both=false}={}){
 const calls=[],scripts=[];
 const c={URL,URLSearchParams,location:new URL(url),localStorage:{getItem(k){if(block)throw Error();return storage.get(k)},setItem(k,v){if(block)throw Error();storage.set(k,v)}},
 document:{referrer:'https://tabobooks.com/pending.html?key=private-key',currentScript:{dataset:{ga4Id:id,ga4Catalog:JSON.stringify({book1:'كتاب تجريبي',book20:'سيجما'})},nonce:'synthetic'},
 createElement:()=>({}),head:{appendChild(s){scripts.push(s);queueMicrotask(()=>fail?s.onerror():s.onload())}}},
 gtag(...args){calls.push(args);if(args[2]?.event_callback)args[2].event_callback();}};
 c.window=c;vm.createContext(c);
 if(both){const tag=c.document.currentScript;c.document.currentScript={dataset:{googleTagId:'AW-18456253761',googleConversionLabel:'Z30jCMCguqYdEMGq0OBE'},nonce:'synthetic'};vm.runInContext(ads,c);c.document.currentScript=tag;}
 vm.runInContext(ga4,c);return {c,calls,scripts,storage};
}
const events=(r,name)=>r.calls.filter(x=>x[0]==='event'&&x[1]===name);
(async()=>{
 const r=runtime({both:true});await flush();assert.equal(r.scripts.length,1);assert.equal(r.calls.filter(x=>x[0]==='js').length,1);
 const config=r.calls.find(x=>x[0]==='config'&&x[1]==='G-SYNTHETIC1')[2];assert.equal(config.send_page_view,false);assert.equal(config.allow_google_signals,false);
 const data={content_ids:['book1'],value:9.99,currency:'USD',email:'private@example.invalid'};
 await r.c.TaboAnalytics.track('ViewContent',data);await r.c.TaboAnalytics.track('ViewContent',data);assert.equal(events(r,'view_item').length,1);
 await r.c.TaboAnalytics.track('AddToCart',data);await r.c.TaboAnalytics.track('InitiateCheckout',data);await r.c.TaboAnalytics.track('InitiateCheckout',data);assert.equal(events(r,'begin_checkout').length,1);
 await r.c.TaboAnalytics.track('PaymentInfo',{...data,payment_type:'PayPal'});assert.equal(events(r,'add_payment_info')[0][2].payment_type,'PayPal');
 await r.c.TaboAnalytics.track('Purchase',data);await r.c.TaboAnalytics.purchase({transaction_id:'a'.repeat(64),value:9.99,currency:'USD',items:[{item_id:'book1'}]});assert.equal(events(r,'purchase').length,0);
 for(const name of ['page_view','view_item','add_to_cart','begin_checkout','add_payment_info'])for(const e of events(r,name)){assert.equal(e[2].send_to,'G-SYNTHETIC1');assert(!JSON.stringify(e).includes('private'));assert(e[2].page_location.includes('utm_source=google'));}
 assert(r.c.location.href.includes('email='));assert.equal(events(r,'page_view').length,1);vm.runInContext(ga4,r.c);await flush();assert.equal(events(r,'page_view').length,1);
 for(const url of ['https://preview.onrender.com/','https://tabobooks.com/order-help','https://tabobooks.com/pending.html?key=x'])assert.equal(runtime({url}).c.TaboAnalytics,undefined);
 assert.equal(runtime({id:''}).c.TaboAnalytics,undefined);
 const purchase={transaction_id:'a'.repeat(64),value:560.6,currency:'EGP',items:[{item_id:'book1'}],email:'private@example.invalid'};
 const paid=runtime({url:'https://tabobooks.com/success.html?order=private'});assert.equal(paid.scripts.length,0);await paid.c.TaboAnalytics.purchase(purchase);assert.equal(paid.scripts.length,0);
 paid.c.location.search='';await Promise.all([paid.c.TaboAnalytics.purchase(purchase),paid.c.TaboAnalytics.purchase(purchase)]);assert.equal(events(paid,'purchase').length,1);assert.equal(events(paid,'page_view').length,0);
 assert.equal(events(paid,'purchase')[0][2].value,560.6);assert(!JSON.stringify(paid.calls).includes('private'));
 const reload=runtime({url:'https://tabobooks.com/success.html',storage:paid.storage});await reload.c.TaboAnalytics.purchase(purchase);assert.equal(reload.scripts.length,0);
 const blocked=runtime({url:'https://tabobooks.com/success.html',block:true});await blocked.c.TaboAnalytics.purchase(purchase);await blocked.c.TaboAnalytics.purchase(purchase);assert.equal(events(blocked,'purchase').length,1);
 const failure=runtime({url:'https://tabobooks.com/success.html',fail:true});await failure.c.TaboAnalytics.purchase(purchase);assert.equal(events(failure,'purchase').length,0);assert.equal(failure.storage.size,0);
 for(const bad of [{...purchase,currency:'SAR'},{...purchase,value:0},{...purchase,transaction_id:'raw-order'},{...purchase,items:[{item_id:'book999'}]}])await blocked.c.TaboAnalytics.purchase(bad);
 assert.equal(events(blocked,'purchase').length,1);
 console.log('GA4: destination isolation, shared Google loader, journey, private data exclusion, confirmed purchase gates and deduplication passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
