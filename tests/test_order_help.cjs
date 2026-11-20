// Synthetic browser, no DB, email delivery or payment requests.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('order-help.js','utf8'),flush=()=>new Promise(r=>setImmediate(r));
function run(){const nodes={},requests=[],errors=[];let verified=false;
 const node=id=>nodes[id]||(nodes[id]={id,hidden:['verifyForm','orderSection'].includes(id),value:'',textContent:'',className:'',children:[],handlers:{},addEventListener(e,fn){this.handlers[e]=fn},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c)},focus(){}});
 const c={localStorage:{getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}},document:{documentElement:{},getElementById:node,createElement:()=>({})},fetch:async(url,opts)=>{
 requests.push({url,opts});let status=200,data={};
 if(url.endsWith('/status')){status=verified?200:403;data={order:{id:'TABO-0000000000000001',status:'PAID',items:[{name:'<Not HTML>'}],total:20,currency:'USD',returnUrl:'/pending.html?key='+'a'.repeat(64)}};}
 if(url.endsWith('/request'))data={challenge:'b'.repeat(64)};
 if(url.endsWith('/verify')){verified=true;data={ok:true};}
 if(url.endsWith('/resend'))data={accepted:true,state:'pending'};
 return {ok:status===200,status,json:async()=>data};}};
 for(const match of fs.readFileSync('order-help.html','utf8').matchAll(/id="([^"]+)"/g))node(match[1]);vm.createContext(c);vm.runInContext(source,c);return {c,nodes,requests,errors};}
(async()=>{const r=run();await flush();assert.equal(r.nodes.orderSection.hidden,true);assert.equal(r.nodes.requestForm.hidden,false);
 r.nodes.orderId.value='TABO-0000000000000001';r.nodes.email.value='reader@example.invalid';
 await r.nodes.requestForm.handlers.submit({preventDefault(){}});assert.equal(r.nodes.verifyForm.hidden,false);assert.equal(r.nodes.requestForm.hidden,true);
 r.nodes.code.value='١٢٣٤٥٦';await r.nodes.verifyForm.handlers.submit({preventDefault(){}});assert.equal(r.nodes.orderSection.hidden,false);assert.equal(r.nodes.email.value,'');assert.equal(r.nodes.code.value,'');
 const req=r.requests.find(x=>x.url.endsWith('/verify'));assert.equal(JSON.parse(req.opts.body).code,'123456');
 assert.equal(r.nodes.books.children[0].textContent,'<Not HTML>');assert(r.nodes.returnLink.href.startsWith('/pending.html?key='));
 await r.nodes.resendButton.handlers.click();assert(r.nodes.message.textContent.includes('استلام'));
 r.nodes.languageToggle.handlers.click();assert.equal(r.c.document.documentElement.lang,'en');assert.equal(r.nodes.title.textContent,'Help with my order');
 assert(r.requests.every(x=>x.url.startsWith('/api/order-help/')&&x.opts.cache==='no-store'));assert(!source.includes('/api/download-session'));
 console.log('Order help: verification forms, Arabic digits, safe text rendering, same-origin APIs and blocked-storage resilience passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
