/* Isolated reader review UI. No requests leave this fixture. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Node{
 constructor(tag='div'){
  this.tag=tag;this.children=[];this.listeners={};this.textContent='';this.value='';
  this.checked=false;this.hidden=false;this.disabled=false;this.className='';this.attributes={};
  this.classList={toggle:(name,active)=>{
   const names=new Set(this.className.split(/\s+/).filter(Boolean));
   if(active)names.add(name);else names.delete(name);
   this.className=[...names].join(' ');
  }};
 }
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child)}}
 replaceChildren(...children){this.children=[];this.append(...children)}
 addEventListener(name,callback){this.listeners[name]=callback}
 setAttribute(name,value){this.attributes[name]=value}
 querySelectorAll(selector){return descendants(this).filter(node=>selector==='input'&&node.tag==='input')}
 querySelector(selector){return selector==='input:checked'?this.querySelectorAll('input').find(node=>node.checked)||null:null}
 set innerHTML(_){throw new Error('Untrusted HTML must never be rendered')}
}
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)])}
function byClass(node,name){return descendants(node).filter(child=>child.className.split(' ').includes(name))}
(async()=>{
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)};
 const choices=Array.from({length:5},(_,index)=>{
  const label=new Node('label'),input=new Node('input');
  input.value=String(index+1);label.append(input);get('commentRating').append(label);
  return input;
 });
 function choose(value){choices.forEach(input=>{input.checked=Number(input.value)===value})}
 const calls=[],events=[],writes=[],values=new Map([['taboLastOrderId','TABO-0000000000000001']]);
 const document={documentElement:{lang:'ar'},getElementById:get,createElement:tag=>new Node(tag)};
 let observer,accept,mode='normal',published=false,postedRating;
 get('commentForm').reportValidity=()=>choices.some(input=>input.checked);
 get('commentForm').reset=()=>{
  for(const id of ['commentEmail','commentName','commentBody','commentWebsite'])get(id).value='';
  get('commentConsent').checked=false;get('commentUpdates').checked=false;choose(null);
 };
 const row={id:'1',display_name:'<script>test</script>',body:'<img src=x onerror=alert(1)> honest negative opinion',
  rating:3,verified_purchase:false,created_at:new Date(Date.now()-2*86400000).toISOString()};
 const c={document,location:{pathname:'/books/book21',search:'?utm_source=facebook&fbclid=synthetic'},Intl,Date,Set,Number,encodeURIComponent,
 MutationObserver:class{constructor(callback){observer=callback}observe(){}},
 localStorage:{getItem:key=>values.get(key)||null,setItem:(...args)=>writes.push(args)},
 TaboTracking:{track:(...args)=>events.push(args)},
 fetch:async(url,options={})=>{
  calls.push({url,options});
  if(options.method==='POST'){
   if(mode==='limited')return {ok:false,status:429};
   postedRating=JSON.parse(options.body).rating;
   await new Promise(resolve=>{accept=resolve});published=true;
   return {ok:true,json:async()=>({ok:true,status:'accepted'})};
  }
  return {ok:true,json:async()=>({comments:url.includes('offset=0')?
   (published?[{...row,id:'3',body:'New comment',rating:postedRating},row]:[row]):
   [{...row,id:'2',rating:null,verified_purchase:true}],total:published?3:2})};
 }};
 vm.createContext(c);vm.runInContext(fs.readFileSync('book-comments.js','utf8'),c);await flush();await flush();
 const firstCard=get('commentsList').children[0];
 assert.equal(descendants(firstCard).find(node=>node.tag==='strong').textContent,row.display_name);
 assert.equal(descendants(firstCard).find(node=>node.tag==='p').textContent,row.body);
 assert.equal(byClass(firstCard,'comment-avatar').length,1);
 assert.equal(byClass(firstCard,'comment-rating').length,1);
 assert.equal(byClass(firstCard,'is-filled').length,3);
 assert.equal(byClass(firstCard,'is-empty').length,2);
 assert.equal(byClass(firstCard,'comment-verified').length,0);
 assert.equal(choices.some(input=>input.checked),false);
 await get('commentsMore').listeners.click();
 assert.equal(get('commentsList').children.length,2);
 const legacy=get('commentsList').children[1];
 assert.equal(byClass(legacy,'comment-verified')[0].textContent,'شراء موثّق');
 assert.equal(byClass(legacy,'comment-rating').length,0);
 const event={preventDefault(){}};
 await get('commentForm').listeners.submit(event);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,0);
 choose(4);get('commentRating').listeners.change();
 assert.equal(get('commentRating').children.filter(label=>label.className.includes('is-selected')).length,4);
 get('commentEmail').value='reader@example.invalid';get('commentName').value='Real reader';
 get('commentBody').value='Honest detailed opinion';get('commentConsent').checked=true;
 document.documentElement.lang='en';observer();
 assert.equal(get('commentEmail').value,'reader@example.invalid');
 assert.equal(get('commentSubmit').textContent,'Publish comment');
 assert.equal(get('commentRatingLabel').textContent,'Your book rating');
 assert.equal(choices[3].attributes['aria-label'],'4 out of 5 stars');
 get('commentUpdates').checked=true;
 const first=get('commentForm').listeners.submit(event);await flush();
 await get('commentForm').listeners.submit(event);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
 const sent=calls.find(call=>call.options.method==='POST');assert.equal(sent.url,'/api/book-comments');
 const data=JSON.parse(sent.options.body);
 assert.equal(data.email,'reader@example.invalid');assert.equal(data.book_id,'book21');assert.equal(data.rating,4);
 assert.equal(data.consent,true);assert.equal(data.updates_consent,true);
 assert.equal(data.order_id,'TABO-0000000000000001');assert(!('verified_purchase'in data));
 accept();await first;
 assert.match(get('commentStatus').textContent,/Thank you/);assert.equal(get('commentEmail').value,'');
 assert.equal(choices.some(input=>input.checked),false);
 assert.equal(byClass(get('commentsList').children[0],'is-filled').length,4);
 mode='limited';choose(2);get('commentEmail').value='reader2@example.invalid';
 get('commentBody').value='Another opinion';get('commentConsent').checked=true;
 await get('commentForm').listeners.submit(event);
 assert.match(get('commentStatus').textContent,/Too many/);
 assert.equal(get('commentEmail').value,'reader2@example.invalid');
 assert.equal(choices[1].checked,true);assert.equal(get('commentSubmit').disabled,false);
 assert.equal(events.length,0);assert.equal(writes.length,0);
 assert.equal(c.location.search,'?utm_source=facebook&fbclid=synthetic');
 assert(calls.every(call=>!call.url.includes('reader@')));
 console.log('Reader reviews passed. Genuine 1–5 stars, legacy comments, accessibility labels, form/reset/retry, privacy, plaintext, verified badges and pagination.');
})().catch(error=>{console.error(error);process.exitCode=1});
