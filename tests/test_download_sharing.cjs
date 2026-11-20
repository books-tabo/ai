/* All downloads, clipboard writes and sharing are synthetic and local. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const transferSource=fs.readFileSync('download-progress.js','utf8');
const sharingSource=fs.readFileSync('book-sharing.js','utf8');
const successSource=fs.readFileSync('success.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const bytes=new TextEncoder().encode('%PDF-synthetic-payload');
function response({chunks=[bytes],length=bytes.length,encoding=null,ok=true,type='application/pdf',remaining='1',failure=false,stream=true}={}){
 let index=0;
 return {ok,headers:{get:name=>({'Content-Type':type,'Content-Length':length===null?null:String(length),'Content-Encoding':encoding,'X-Downloads-Remaining':remaining})[name]??null},
  json:async()=>({error:'Synthetic server rejection'}),blob:async()=>new Blob(chunks,{type}),
  body:stream?{getReader:()=>({read:async()=>{if(failure&&index===1)throw Error('disconnected');return index<chunks.length?{value:chunks[index++],done:false}:{done:true};},cancel:async()=>{},releaseLock(){}})}:null};
}
function transferRuntime(reply){
 const calls=[],timers=[],saved=[];
 const c={window:{},Blob,AbortController,Uint8Array,URL:{createObjectURL:()=>{saved.push('url');return 'blob:synthetic';},revokeObjectURL:()=>{}},
  document:{body:{appendChild(){}},createElement:()=>({click(){saved.push('click')},remove(){}})},
  fetch:async(url,options)=>{calls.push({url,options});return typeof reply==='function'?reply(options):reply;},
  setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},clearTimeout(){}};
 vm.runInNewContext(transferSource,c);return {c,calls,timers,saved,api:c.window.TaboDownload};
}
async function transferChecks(){
 const t=transferRuntime(response({chunks:[bytes.slice(0,6),bytes.slice(6)]})),states=[];
 const file=await t.api.receive('book22','TABO-SYNTHETIC',state=>states.push(state));
 assert.equal(states[0].phase,'preparing');assert.equal(states.at(-1).phase,'received');
 assert(states.some(s=>s.loaded===6&&s.total===bytes.length));assert.equal(file.remaining,1);
 assert.equal(await file.blob.text(),'%PDF-synthetic-payload');
 assert.equal(t.calls[0].url,'/api/download/book22');assert.equal(t.calls[0].options.credentials,'same-origin');
 assert.equal(JSON.parse(t.calls[0].options.body).order_id,'TABO-SYNTHETIC');
 t.api.save(file.blob,'book22');assert.deepEqual(t.saved,['url','click']);assert.equal(t.calls.length,1);
 for(const options of [{length:bytes.length+1},{type:'text/html'},{chunks:[new TextEncoder().encode('<html>bad</html>')]},{chunks:[bytes.slice(0,6),bytes.slice(6)],failure:true},{ok:false}]){
  const bad=transferRuntime(response(options)),progress=[];
  await assert.rejects(bad.api.receive('book22','synthetic',s=>progress.push(s)));
  assert(!progress.some(s=>s.phase==='received'));assert.equal(bad.saved.length,0);assert.equal(bad.calls.length,1);
 }
 for(const options of [{length:null},{encoding:'gzip',length:1},{stream:false}]){
  const fallback=transferRuntime(response(options)),progress=[];
  await fallback.api.receive('book1','synthetic',s=>progress.push(s));
  if(options.length===null||options.encoding)assert(progress.filter(s=>s.phase==='downloading').every(s=>s.total===null));
 }
 let slow;
 slow=transferRuntime(options=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted')))));
 const pending=slow.api.receive('book1','synthetic',()=>{});slow.timers[0].fn();
 await assert.rejects(pending,error=>error.code==='timeout');assert.equal(slow.calls.length,1);
}
function node(){return {textContent:'',hidden:true,disabled:false,value:0,dataset:{},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},focus(){this.focused=true},select(){this.selected=true}};}
async function sharingChecks(){
 for(const canCopy of [true,false]){
  const nodes=new Map(),copied=[],get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
  const c={window:{},document:{getElementById:get},navigator:{clipboard:{writeText:async value=>{if(!canCopy)throw Error('blocked');copied.push(value)}}},location:{search:'?key=private&email=buyer@example.invalid'}};
  vm.runInNewContext(sharingSource,c);
  for(const language of ['ar','en']){
   const title=language==='ar'?'عنوان <خاص> & اختبار':'A special title';
   c.window.TaboBookSharing.render({id:'book22',title,language});
   const wa=new URL(get('shareWhatsApp').href);
   assert.equal(wa.origin,'https://wa.me');assert.equal(wa.searchParams.get('text'),title+'\nhttps://tabobooks.com/books/book22');
   assert(!wa.href.includes('private'));await get('copyBookLink').onclick();
   if(canCopy)assert.equal(copied.at(-1),'https://tabobooks.com/books/book22');
   else{assert.equal(get('bookShareURL').hidden,false);assert(get('bookShareURL').selected);}
   assert.equal(get('copyBookLink').disabled,false);
  }
 }
}
async function receiptChecks(){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
 const requests=[],transfers=[],saves=[];let remaining=2,fail=true,slow=false,release;
 const c={window:{TaboDownload:{receive:async(id,order,onProgress)=>{transfers.push(id);onProgress({phase:'preparing'});if(slow)await new Promise(resolve=>release=resolve);if(fail){remaining=1;throw Error('network');}onProgress({phase:'received',loaded:10,total:10});remaining=0;return {blob:new Blob(['%PDF-fake']),remaining};},save:(blob,id)=>saves.push(id)}},
  document:{documentElement:{},getElementById:get,querySelectorAll:()=>[]},localStorage:{getItem:()=>null},
  location:{pathname:'/success.html',search:'?order=TABO-SYNTHETIC'},history:{state:null},URLSearchParams,Map,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>({books:[{id:'book22',name:'<unsafe>',remaining}]})}}};
 vm.createContext(c);vm.runInContext(successSource,c);await flush();
 assert(get('content').innerHTML.includes('&lt;unsafe&gt;'));
 await vm.runInContext('downloadBook("book22")',c);assert.equal(transfers.length,1);assert.equal(saves.length,0);
 assert.equal(get('download-state-book22').dataset.phase,'error');assert(get('content').innerHTML.includes('إعادة المحاولة'));
 fail=false;slow=true;const pending=vm.runInContext('downloadBook("book22")',c);await flush();
 await vm.runInContext('downloadBook("book22")',c);assert.equal(transfers.length,2);release();await pending;
 assert.equal(get('download-state-book22').dataset.phase,'done');assert.equal(saves.length,1);
 await vm.runInContext('downloadBook("book22")',c);assert.equal(transfers.length,2);assert.equal(saves.length,2);
 // A failed last allowed transfer must not be retried after the manifest says zero.
 vm.runInContext('cachedFile=null',c);remaining=0;fail=true;
 const before=transfers.length;await vm.runInContext('downloadBook("book22")',c);assert.equal(transfers.length,before);
 assert(!requests.some(u=>u.includes('Purchase')));
}
(async()=>{await transferChecks();await sharingChecks();await receiptChecks();console.log('Download/share: real byte progress, unknown lengths, truncated/error/timeout rejection, entitlement refresh, duplicate prevention, cached re-save, canonical WhatsApp links and clipboard fallback passed.');})().catch(error=>{console.error(error);process.exitCode=1});
