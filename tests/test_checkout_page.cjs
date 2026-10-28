/* Real shared checkout/page scripts with independent, synthetic HTTP fixtures. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const html=fs.readFileSync('checkout.html','utf8'),core=fs.readFileSync('checkout-core.js','utf8');
const fixtures=JSON.parse(execFileSync('python3',['-c',`import json,catalog,localization,seo
print(json.dumps({'books':[{'id':key,**{k:b[k] for k in ('name','enName','fixedPrices','bundleEligible','previewEnabled') if k in b}} for key,b in seo.BOOKS.items()], 'usd':localization.pricing_for_country(catalog.PRICES,'KW'),'egp':localization.pricing_for_country(catalog.PRICES,'EG')}))`],{encoding:'utf8'}));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function node(){return {textContent:'',hidden:false,disabled:false,value:'',style:{},parentElement:{},classList:{toggle(){},add(){}},events:{},setAttribute(){},addEventListener(name,callback){this.events[name]=callback;}};}
async function boot(saved=JSON.stringify([{id:'book20'},{id:'book21'}])){
 const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],node()]));
 nodes['checkout-catalog'].textContent=JSON.stringify(fixtures.books);
 nodes.checkoutName.value='Synthetic Buyer';nodes.checkoutEmail.value='fixture@example.invalid';nodes.checkoutPhone.value='01000000000';
 const values=new Map([['taboBooksCart',saved],['taboLanguage','ar'],['taboCountry','KW']]);
 const calls=[],redirects=[],events=[],alerts=[],session=new Map();
 const c={document:{documentElement:{},getElementById:id=>nodes[id]},navigator:{language:'ar-KW'},
  localStorage:{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)},
  sessionStorage:{setItem:(key,value)=>session.set(key,value)},location:{origin:'https://synthetic.invalid',search:'?utm_source=instagram&fbclid=synthetic'},
  window:{location:{assign:value=>redirects.push(value)},TaboTracking:{track:(...args)=>events.push(args)}},
  Date,Intl,Number,JSON,URL,URLSearchParams,AbortController,setTimeout:()=>1,clearTimeout(){},alert:message=>alerts.push(message)};
 c.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url.startsWith('/api/store-config')){
   const egp=new URL(url,c.location.origin).searchParams.get('country')==='EG';
   return {ok:true,json:async()=>({...fixtures[egp?'egp':'usd'],checkoutEnabled:true,checkoutSupported:true,paymentProvider:'kashier',paypalAvailable:!egp})};
  }
  if(url==='/api/checkout-quote')return {ok:true,json:async()=>({ok:true,quote:{displayCurrency:'USD',displayTotal:'20.00',chargeCurrency:'EGP',chargeTotal:'1122.32',expiresAt:Date.now()/1000+300,token:'synthetic-quote'}})};
  if(url==='/api/orders'){
   const paypal=JSON.parse(options.body).paymentProvider==='paypal';
   return {ok:true,json:async()=>({ok:true,order:{id:'TABO-0000000000000001',items:[{id:'book20'},{id:'book21'}],total:paypal?20:1122.32,currency:paypal?'USD':'EGP'},payment_url:paypal?'https://www.sandbox.paypal.com/checkoutnow?token=PAYPALORDER1234567':'https://payments.kashier.io/session/synthetic',tracking_url:'/pending.html?key='+'a'.repeat(64)})};
  }
  throw Error('Unexpected synthetic request');
 };
 vm.createContext(c);vm.runInContext(fs.readFileSync('offer-pricing.js','utf8')+'\n'+core+'\n'+fs.readFileSync('checkout-page.js','utf8'),c);
 await flush();await flush();await flush();return {c,nodes,calls,redirects,events,alerts,values,session};
}
async function pendingCase(status,ready,activationFails=false){
 const source=fs.readFileSync('pending.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
 const nodes=new Map(),calls=[],redirects=[],timers=[];let failed=false;
 const c={document:{documentElement:{},getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)}},localStorage:{getItem:()=> 'ar'},
  sessionStorage:{getItem:()=>null,removeItem(){}},location:{search:'?key='+'a'.repeat(64),replace:value=>redirects.push(value)},
  URLSearchParams,encodeURIComponent,Intl,Number,Math,setTimeout:(callback,delay)=>{timers.push({callback,delay});return 1;},
  fetch:async(url,options)=>{
   calls.push({url,options});if(url.startsWith('/api/order-status'))return {ok:true,json:async()=>({status,ready,order_id:'TABO-0000000000000001',books:['Synthetic Book'],pricing:{displayCurrency:'USD',displayTotal:'20',chargeCurrency:'USD',chargeTotal:'20'}})};
   assert.equal(url,'/api/download-session');if(activationFails&&!failed){failed=true;return {ok:false,json:async()=>({error:'Temporary synthetic outage'})};}
   return {ok:true,json:async()=>({ok:true,success_url:'/success.html?order=TABO-0000000000000001'})};
  }};
 vm.createContext(c);vm.runInContext(source,c);await flush();await flush();
 if(status!=='PAID'||!ready){assert.equal(calls.length,1);assert.equal(redirects.length,0);return;}
 assert.equal(nodes.get('payment').hidden,true);
 if(activationFails){assert.equal(redirects.length,0);assert.equal(timers.length,1);assert(timers[0].delay>=10000);timers[0].callback();await flush();await flush();}
 assert.deepEqual(redirects,['/success.html?order=TABO-0000000000000001']);
 assert(calls.some(call=>call.url==='/api/download-session'&&call.options.method==='POST'));
}
(async()=>{
 for(const method of ['card','paypal']){
  const {nodes,calls,redirects,events,session}=await boot();
  assert.equal(nodes.checkoutContent.hidden,false);assert.equal(nodes.emptyCart.hidden,true);
  assert.match(nodes.cartFinal.textContent,/20/);assert.equal(nodes.paypalCheckoutBtn.hidden,false);
  assert.equal(calls.filter(call=>call.url==='/api/orders').length,0);
  await nodes[method==='card'?'checkoutBtn':'paypalCheckoutBtn'].events.click();
  assert.deepEqual(redirects,[method==='card'?'https://payments.kashier.io/session/synthetic':'https://www.sandbox.paypal.com/checkoutnow?token=PAYPALORDER1234567']);
  assert.equal(events.filter(event=>event[0]==='InitiateCheckout').length,1);assert(!events.some(event=>event[0]==='Purchase'));
  assert.equal(session.size,1);
 }
 {
  const {c,nodes,calls}=await boot();await c.loadStoreConfig('EG');
  assert.equal(nodes.paypalCheckoutBtn.hidden,true);assert.match(nodes.cartFinal.textContent,/1,000|1000/);
  nodes.checkoutEmail.value='';await nodes.checkoutBtn.events.click();assert(!calls.some(call=>call.url==='/api/orders'));
  nodes.languageToggle.events.click();assert.equal(c.document.documentElement.lang,'en');
 }
 for(const saved of ['not-json','null','{}','[]']){const {nodes,calls}=await boot(saved);assert.equal(nodes.emptyCart.hidden,false);assert.equal(nodes.checkoutContent.hidden,true);assert(!calls.some(call=>call.url==='/api/orders'));}
 {
  const source=fs.readFileSync('index.html','utf8').split('/* Checkout navigation start */')[1].split('/* Checkout navigation end */')[0];
  const redirects=[];const c={URLSearchParams,location:{search:'?cart=1&offer=sigma-couple&utm_source=facebook&utm_campaign=checkout&fbclid=a%2Bb&utm_term=one&utm_term=two'},window:{location:{assign:url=>redirects.push(url)}}};
  vm.createContext(c);vm.runInContext(source,c);c.openCartFromQuery();assert.equal(redirects.length,0);c.openCart();const url=new URL(redirects[0],'https://synthetic.invalid');
  assert.equal(url.pathname,'/checkout');assert.equal(url.searchParams.get('fbclid'),'a+b');assert.equal(url.searchParams.get('utm_source'),'facebook');assert.deepEqual(url.searchParams.getAll('utm_term'),['one','two']);assert(!url.searchParams.has('offer'));assert(!url.searchParams.has('cart'));
 }
 await pendingCase('PENDING',false);await pendingCase('PENDING',true);await pendingCase('PAID',false);await pendingCase('PAID',true);await pendingCase('PAID',true,true);
 console.log('Checkout page: both direct gateways, exact shared pack prices, EGP exclusion, preserved ad params, empty carts, no premature Purchase and automatic device-bound paid delivery/retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
