/* Independent DOM fixture: email privacy, plain text, locale, moderation, no events. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.textContent='';this.value='';this.checked=false;this.hidden=false;this.disabled=false;}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=[...children]}
 addEventListener(name,callback){this.listeners[name]=callback}
 reportValidity(){return true}
 set innerHTML(_){throw new Error('Untrusted HTML must never be rendered')}
}
(async()=>{
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)};
 const calls=[],events=[],writes=[],values=new Map([['taboLastOrderId','TABO-0000000000000001']]);
 const document={documentElement:{lang:'ar'},getElementById:get,createElement:tag=>new Node(tag)};
 let observer,accept,mode='normal';
 get('commentForm').reset=()=>{for(const id of ['commentEmail','commentName','commentBody','commentWebsite'])get(id).value='';get('commentConsent').checked=false};
 const row={id:'1',display_name:'<script>test</script>',body:'<img src=x onerror=alert(1)> honest negative opinion',verified_purchase:false,created_at:'2026-10-28T00:00:00Z'};
 const c={document,location:{pathname:'/books/book21',search:'?utm_source=facebook&fbclid=synthetic'},Intl,Date,Set,Number,encodeURIComponent,
 MutationObserver:class{constructor(callback){observer=callback}observe(){}},
 localStorage:{getItem:key=>values.get(key)||null,setItem:(...args)=>writes.push(args)},
 TaboTracking:{track:(...args)=>events.push(args)},
 fetch:async(url,options={})=>{
  calls.push({url,options});
  if(options.method==='POST'){
   if(mode==='limited')return {ok:false,status:429};
   await new Promise(resolve=>{accept=resolve});return {ok:true,json:async()=>({ok:true,status:'pending'})};
  }
  return {ok:true,json:async()=>({comments:url.includes('offset=0')?[row]:[{...row,id:'2',verified_purchase:true}],total:2})};
 }};
 vm.createContext(c);vm.runInContext(fs.readFileSync('book-comments.js','utf8'),c);await flush();await flush();
 assert.equal(get('commentsList').children[0].children[0].children[0].textContent,row.display_name);
 assert.equal(get('commentsList').children[0].children[1].textContent,row.body);
 assert.equal(get('commentsList').children[0].children[0].children.length,2); // no fabricated badge
 await get('commentsMore').listeners.click();
 assert.equal(get('commentsList').children.length,2);
 assert.equal(get('commentsList').children[1].children[0].children[1].textContent,'شراء موثّق');
 get('commentEmail').value='reader@example.test';get('commentName').value='Real reader';get('commentBody').value='Honest detailed opinion';get('commentConsent').checked=true;
 document.documentElement.lang='en';observer();assert.equal(get('commentEmail').value,'reader@example.test');
 assert.equal(get('commentSubmit').textContent,'Submit comment for review');
 const event={preventDefault(){}};
 const first=get('commentForm').listeners.submit(event);await flush();
 await get('commentForm').listeners.submit(event);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
 const sent=calls.find(call=>call.options.method==='POST');assert.equal(sent.url,'/api/book-comments');
 const data=JSON.parse(sent.options.body);assert.equal(data.email,'reader@example.test');assert.equal(data.book_id,'book21');assert.equal(data.consent,true);
 assert.equal(data.order_id,'TABO-0000000000000001');assert(!('verified_purchase'in data));
 accept();await first;
 assert.match(get('commentStatus').textContent,/moderation/);assert.equal(get('commentEmail').value,'');
 assert.equal(get('commentsList').children.length,2); // pending comment not displayed as a public review
 mode='limited';get('commentEmail').value='reader2@example.test';get('commentBody').value='Another opinion';get('commentConsent').checked=true;
 await get('commentForm').listeners.submit(event);
 assert.match(get('commentStatus').textContent,/Too many/);assert.equal(get('commentEmail').value,'reader2@example.test');assert.equal(get('commentSubmit').disabled,false);
 assert.equal(events.length,0);assert.equal(writes.length,0);
 assert.equal(c.location.search,'?utm_source=facebook&fbclid=synthetic');
 assert(calls.every(call=>!call.url.includes('reader@')));
 console.log('Reader comments: plain text/XSS, private email, pending moderation, verified badge, pagination, language/focus, duplicate submit, 429, no events or URL changes passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
