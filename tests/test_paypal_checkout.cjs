/* Actual optional PayPal storefront and return scripts; synthetic network only. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
const quote=html.split('/* EGP quote start */')[1].split('/* EGP quote end */')[0];
const choice='function renderPayPalChoice(){'+html.split('function renderPayPalChoice(){')[1].split('async function checkoutWithPaymentLink(){')[0];
const checkout='async function checkoutWithPaymentLink(){'+html.split('async function checkoutWithPaymentLink(){')[1].split('window.TABO_CHECKOUT_READY=false;')[0];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function boot(){
 const nodes={checkoutBtn:{disabled:false},paypalCheckoutBtn:{disabled:false},paypalCheckoutNote:{},chargeCurrencyNote:{style:{}},checkoutName:{value:'Synthetic buyer'},checkoutEmail:{value:'test@example.invalid'},checkoutPhone:{value:'01000000000'}};
 const calls=[],events=[],alerts=[],redirects=[];
 const context={document:{getElementById:id=>nodes[id]},storeConfig:{paypalAvailable:true,paymentProvider:'kashier',currency:'USD',country:'KW',checkoutSupported:true},
 cart:[{id:'book20'},{id:'book21'}],currentLanguage:'ar',calculatePricing:()=>({final:20,count:2}),BACKEND_URL:'https://synthetic.invalid',
 location:{origin:'https://synthetic.invalid',search:'?utm_source=facebook&fbclid=synthetic'},
 window:{TABO_CHECKOUT_READY:true,TaboTracking:{track:(...args)=>events.push(args)},location:{assign:value=>redirects.push(value)}},
 localStorage:{setItem(){}},sessionStorage:{setItem(){}},alert:value=>alerts.push(value),t:value=>value,
 URL,JSON,Date,Number,Intl,AbortController,setTimeout:()=>1,clearTimeout(){}};
 context.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({ok:true,order:{id:'TABO-0000000000000002',total:20,currency:'USD',items:[{id:'book20'},{id:'book21'}]},payment_url:'https://www.sandbox.paypal.com/checkoutnow?token=PAYPALORDER1234567',tracking_url:'/pending.html?key='+'2'.repeat(64)})}};
 vm.createContext(context);vm.runInContext(quote+choice+checkout,context);return {context,nodes,calls,events,alerts,redirects};
}
(async()=>{
 {
  const {context:c,nodes,calls,events,redirects}=boot();
  c.renderPayPalChoice();assert.equal(nodes.paypalCheckoutBtn.hidden,false);assert.match(nodes.paypalCheckoutNote.textContent,/الدولار/);
  assert.equal(c.needsEgpQuote(),true); // Existing Kashier conversion is preserved.
  await c.checkoutWithPaymentLink('paypal');
  assert.equal(calls.length,1);assert.match(calls[0].url,/\/api\/orders$/);
  const body=JSON.parse(calls[0].options.body);assert.equal(body.paymentProvider,'paypal');assert(!('quoteToken'in body));
  assert.equal(events.length,1);assert.equal(events[0][0],'InitiateCheckout');assert.equal(events[0][1].currency,'USD');assert.equal(events[0][1].value,20);
  assert.equal(redirects.length,1);assert.equal(c.location.search,'?utm_source=facebook&fbclid=synthetic');
 }
 {
  const {context:c,nodes,calls}=boot();c.storeConfig.paypalAvailable=false;c.renderPayPalChoice();
  assert.equal(nodes.paypalCheckoutBtn.hidden,true);await c.checkoutWithPaymentLink('paypal');assert.equal(calls.length,0);
  c.storeConfig.paypalAvailable=true;c.storeConfig.currency='EGP';c.renderPayPalChoice();assert.equal(nodes.paypalCheckoutBtn.hidden,true);
  await c.checkoutWithPaymentLink('paypal');assert.equal(calls.length,0);
 }
 {
  const {context:c,nodes,calls}=boot();let resolve;
  c.fetch=(url,options)=>{calls.push({url,options});return new Promise(r=>resolve=r)};
  const pending=c.checkoutWithPaymentLink('paypal');await flush();
  await c.checkoutWithPaymentLink('paypal');await c.checkoutWithPaymentLink();
  assert.equal(calls.length,1);assert.equal(nodes.checkoutBtn.disabled,true);assert.equal(nodes.paypalCheckoutBtn.disabled,true);
  resolve({ok:false,json:async()=>({error:'Synthetic outage'})});await pending;
  assert.equal(nodes.checkoutBtn.disabled,false);assert.equal(nodes.paypalCheckoutBtn.disabled,false);
 }
 const returnHtml=fs.readFileSync('paypal-return.html','utf8');
 assert.match(returnHtml,/noindex,nofollow,noarchive/);assert(!/meta-tracking|google-ads-tracking|snap-tracking/.test(returnHtml));
 const script=returnHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
 async function returnPage(paid,valid=true){
  const nodes=new Map(),calls=[],redirects=[],history=[];const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',addEventListener(){}});return nodes.get(id)};
  const c={document:{documentElement:{},getElementById:node},localStorage:{getItem:()=> 'ar'},
   location:{search:valid?'?key='+'2'.repeat(64)+'&token=PAYPALORDER1234567':'?key=invalid',pathname:'/paypal-return.html',replace:value=>redirects.push(value)},
   history:{replaceState:(...args)=>history.push(args)},URLSearchParams,encodeURIComponent,
   fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({ok:true,paid})}}};
  vm.createContext(c);vm.runInContext(script,c);await flush();await flush();
  assert.equal(history[0][2],'/paypal-return.html');
  if(valid){assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/paypal/capture');assert.equal(JSON.parse(calls[0].options.body).token,'PAYPALORDER1234567')}
  else assert.equal(calls.length,0);
  assert.equal(redirects.length,valid&&paid?1:0);if(valid&&!paid)assert.match(node('state').textContent,/لم يؤكد/);
 }
 await returnPage(true);await returnPage(false);await returnPage(false,false);
 console.log('PayPal UI: disabled-by-default/EGP hidden, direct USD, unchanged Kashier quote, duplicate prevention, no premature Purchase, return URL cleanup and paid-only continuation passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
