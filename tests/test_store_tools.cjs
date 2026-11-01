'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('error-monitoring.js','utf8');
function runtime({blocked=false}={}){
 const loaders=[],messages=[];let options;
 const Sentry={globalHandlersIntegration:opts=>({name:'GlobalHandlers',opts}),init:opts=>{options=opts},captureMessage:(message,context)=>messages.push({message,context})};
 const c={URL,location:{origin:'https://tabobooks.com',pathname:'/success.html',search:'?order=SECRET&key=PRIVATE',hash:'#token'},document:{currentScript:{nonce:'nonce123',dataset:{sentryDsn:'https://'+'a'.repeat(32)+'@o123.ingest.us.sentry.io/456',release:'tabo-'+'b'.repeat(16)}},head:{appendChild:n=>loaders.push(n)},createElement:()=>({})},Sentry};c.window=c;vm.createContext(c);vm.runInContext(source,c);
 if(!blocked)loaders[0].onload();
 return {c,loaders,messages,get options(){return options}};
}
const r=runtime();
assert.equal(r.loaders.length,1);assert.equal(r.loaders[0].async,true);assert.equal(r.loaders[0].nonce,'nonce123');
assert.equal(r.loaders[0].referrerPolicy,'no-referrer');assert(r.loaders[0].integrity.startsWith('sha384-'));
assert.equal(r.options.defaultIntegrations,false);assert.equal(r.options.integrations.length,1);
assert.equal(r.options.traceLifecycle,'static');assert.equal(r.options.maxBreadcrumbs,0);assert.equal(r.options.sendClientReports,false);assert.equal(r.options.autoSessionTracking,false);
assert.equal(r.options.dataCollection.userInfo,false);assert.equal(r.options.dataCollection.cookies,false);assert.equal(r.options.dataCollection.urlQueryParams,false);assert.equal(r.options.dataCollection.httpBodies.length,0);
const event={event_id:'1'.repeat(32),timestamp:1234,user:{email:'BUYER',ip_address:'PRIVATE'},request:{url:'https://tabobooks.com/success.html?order=SECRET',cookies:'COOKIE',headers:{Authorization:'TOKEN'}},breadcrumbs:[{message:'PHONE'}],extra:{buyer:'NAME'},contexts:{trace:{data:'SECRET'}},message:'CARD',tags:{email:'BUYER'},exception:{values:[{type:'TypeError',value:'BUYER SECRET PHONE',stacktrace:{frames:[{filename:'https://tabobooks.com/checkout-core.js?v=123&key=SECRET',function:'PRIVATE',lineno:30,colno:9,vars:{buyer:'PRIVATE'},pre_context:['CARD']},{filename:'https://evil.com/tracker.js?SECRET',lineno:10}]}}]}};
const cleaned=r.options.beforeSend(event),serialized=JSON.stringify(cleaned);
for(const secret of ['SECRET','PRIVATE','BUYER','COOKIE','TOKEN','PHONE','CARD','NAME','breadcrumbs','pre_context'])assert(!serialized.includes(secret),secret);
assert.equal(cleaned.request.url,'https://tabobooks.com/success.html');
assert.equal(cleaned.exception.values[0].type,'TypeError');assert.equal(cleaned.exception.values[0].stacktrace.frames.length,1);
assert.equal(cleaned.exception.values[0].stacktrace.frames[0].filename,'https://tabobooks.com/checkout-core.js');
assert.equal(r.options.beforeSend(event),null,'identical errors deduplicated');
assert.equal(r.options.beforeSend({type:'transaction'}),null);assert.equal(r.options.beforeSend({message:'BUYER'}),null);
r.c.TaboErrors.capture('checkout_config_failed');r.c.TaboErrors.capture('buyer@example.com');assert.equal(r.messages.length,1);
const coded=r.options.beforeSend({message:'SECRET',tags:{code:'checkout_config_failed',customer:'BUYER'}});assert.equal(coded.message,'checkout_config_failed');assert(!JSON.stringify(coded).includes('BUYER'));
const blocked=runtime({blocked:true});for(let i=0;i<10;i++)blocked.c.TaboErrors.capture('download_transfer_failed');assert.equal(blocked.messages.length,0);
blocked.loaders[0].onload();assert.equal(blocked.messages.length,1,'queued failures are bounded and deduplicated');
const failed=runtime({blocked:true});failed.loaders[0].onerror();assert.doesNotThrow(()=>failed.c.TaboErrors.capture('download_transfer_failed'));
const offline=runtime();offline.c.Sentry.captureMessage=()=>{throw Error('offline')};assert.doesNotThrow(()=>offline.c.TaboErrors.capture('checkout_config_failed'));
console.log('Store tools: privacy allowlist, optional loading, CSP nonce and failure isolation passed.');
function toolsUI(ok=true,enabled=false){
 class Node{constructor(){this.children=[];this.handlers={};this.textContent=''}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=items}addEventListener(name,fn){this.handlers[name]=fn}}
 const nodes=Object.fromEntries(['toolsMessage','speedLinks','sentryState','sentryLink','toolsReload'].map(id=>[id,new Node()])),calls=[];
 const c={document:{getElementById:id=>nodes[id],createElement:()=>new Node()},fetch:async(url,options)=>{calls.push({url,options});return {ok,json:async()=>({pagespeed:[{label:'الرئيسية',mobile:'https://pagespeed.web.dev/analysis?url=https%3A%2F%2Ftabobooks.com%2F&form_factor=mobile',desktop:'https://pagespeed.web.dev/analysis?url=https%3A%2F%2Ftabobooks.com%2F&form_factor=desktop'}],sentry:{enabled,url:'https://sentry.io/'}})}}};c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync('admin-tools.js','utf8'),c);return {c,nodes,calls};
}
(async()=>{
 const ui=toolsUI();assert.equal(ui.calls.length,0,'never fetch tools before owner selects panel');await ui.c.TaboStoreTools.open('orders');assert.equal(ui.calls.length,0);
 await ui.c.TaboStoreTools.open('tools');assert.equal(ui.calls[0].url,'/api/admin-dashboard/tools');assert.equal(ui.calls[0].options.credentials,'same-origin');assert.equal(ui.nodes.speedLinks.children.length,1);
 const links=ui.nodes.speedLinks.children[0].children.slice(1);assert.equal(links.length,2);assert(links.every(a=>a.target==='_blank'&&a.rel==='noopener noreferrer'));
 assert(ui.nodes.sentryState.textContent.includes('يتبقى'));const active=toolsUI(true,true);await active.c.TaboStoreTools.open('tools');assert(active.nodes.sentryState.textContent.includes('مفعّل'));
 const denied=toolsUI(false);await denied.c.TaboStoreTools.open('tools');assert.equal(denied.nodes.speedLinks.children.length,0);assert(denied.nodes.toolsMessage.textContent.includes('تسجيل الدخول'));
 console.log('Owner tools: on-demand authenticated request, audit links, honest activation status and access failure passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
