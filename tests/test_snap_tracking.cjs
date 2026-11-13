// Advertising is completely stubbed: no real or synthetic event reaches Snapchat.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'snap-tracking.js'), 'utf8');
const receiptScript = fs.readFileSync(path.join(__dirname, '..', 'success.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = {transaction_id: 'a'.repeat(64), value: 829.27, currency: 'EGP', content_ids: ['book20']};

function runtime({url='https://tabobooks.com/', storage=new Map(), blockedStorage=false,
    fail=false, snapThrows=false, metaThrows=false}={}) {
    const calls = [], appended = [], meta = [];
    const context = {URL, URLSearchParams, location: new URL(url),
        document: {currentScript: {dataset:{snapPixelId:'ec1f266b-eb95-49e9-b53d-e6d5b38e1888'}, nonce:'test-nonce'},
            createElement:()=>({}), head:{appendChild(script) {
                appended.push(script); queueMicrotask(()=>fail ? script.onerror() : script.onload());
            }}},
        localStorage:{getItem(key) {if(blockedStorage)throw Error('disabled'); return storage.get(key);},
            setItem(key,value) {if(blockedStorage)throw Error('disabled'); storage.set(key,value);}},
        snaptr(...args) {if(snapThrows)throw Error('blocked'); calls.push(args);},
        TaboTracking:{track(...args) {meta.push(args); if(metaThrows)throw Error('Meta blocked');}, purchase:()=>42}};
    context.window=context; vm.createContext(context); vm.runInContext(source,context);
    return {context,calls,appended,storage,meta};
}
const purchases = r => r.calls.filter(x=>x[0]==='track' && x[1]==='PURCHASE');

(async()=>{
    const landing=runtime(); await flush();
    assert.equal(landing.appended[0].src,'https://sc-static.net/scevent.min.js');
    assert.equal(landing.appended[0].nonce,'test-nonce');
    assert.equal(Object.keys(landing.calls[0][2]).length,0);
    assert.equal(landing.context.TaboTracking.purchase(),42);
    await landing.context.TaboTracking.track('ViewContent',{...payload,user_email:'private@example.invalid'});
    await landing.context.TaboTracking.track('AddToCart',payload);
    await landing.context.TaboTracking.track('InitiateCheckout',payload);
    assert.deepEqual(landing.calls.filter(x=>x[0]==='track').map(x=>x[1]),['PAGE_VIEW','VIEW_CONTENT','ADD_CART','START_CHECKOUT']);
    assert.equal(landing.meta.length,3);
    assert(!JSON.stringify(landing.calls).includes('private@example.invalid'));
    for(const name of ['Purchase','PURCHASE','constructor','toString']) await landing.context.TaboSnapTracking.track(name,payload);
    await landing.context.TaboSnapTracking.purchase(payload);
    assert.equal(purchases(landing).length,0);
    vm.runInContext(source,landing.context); await flush(); assert.equal(landing.appended.length,1);
    const review=runtime({url:'https://tabo-books-seo-review.onrender.com/'});
    assert.equal(review.context.TaboSnapTracking,undefined); assert.equal(review.appended.length,0);

    const receipt=runtime({url:'https://tabobooks.com/success.html?order=private#token'});
    assert.equal(receipt.appended.length,0); await receipt.context.TaboSnapTracking.purchase(payload);
    assert.equal(receipt.appended.length,0);
    receipt.context.location.search=''; receipt.context.location.hash='';
    for(const bad of [null,{...payload,value:0},{...payload,value:'829.27'},
        {...payload,currency:'WRONG'},{...payload,transaction_id:'provider-private'},
        {...payload,content_ids:['private-value']}]) await receipt.context.TaboSnapTracking.purchase(bad);
    assert.equal(receipt.appended.length,0);
    await Promise.all([receipt.context.TaboSnapTracking.purchase({...payload,user_phone_number:'private'}),
        receipt.context.TaboSnapTracking.purchase(payload)]);
    assert.equal(purchases(receipt).length,1);
    const event=purchases(receipt)[0][2];
    assert.equal(event.price,829.27); assert.equal(event.currency,'EGP');
    assert.equal(event.transaction_id,payload.transaction_id); assert.equal(event.client_dedup_id,payload.transaction_id);
    assert.equal(event.item_ids[0],'book20'); assert.equal(event.number_items,1);
    assert(!JSON.stringify(receipt.calls).includes('private'));
    const refreshed=runtime({url:'https://tabobooks.com/success.html',storage:receipt.storage});
    await refreshed.context.TaboSnapTracking.purchase(payload); assert.equal(refreshed.appended.length,0);
    const blocked=runtime({url:'https://tabobooks.com/success.html',fail:true});
    await blocked.context.TaboSnapTracking.purchase(payload); assert.equal(purchases(blocked).length,0);
    assert.equal(blocked.storage.size,0);
    const noStorage=runtime({url:'https://tabobooks.com/success.html',blockedStorage:true});
    await noStorage.context.TaboSnapTracking.purchase(payload); await noStorage.context.TaboSnapTracking.purchase(payload);
    assert.equal(purchases(noStorage).length,1);
    const independent=runtime({metaThrows:true});
    await independent.context.TaboTracking.track('AddToCart',payload);
    assert(independent.calls.some(x=>x[1]==='ADD_CART'));
    const sdkThrows=runtime({url:'https://tabobooks.com/success.html',snapThrows:true});
    await sdkThrows.context.TaboSnapTracking.purchase(payload); assert.equal(sdkThrows.storage.size,0);

    async function receiptDocument({ok=true,snap=payload,snapThrows=false}={}) {
        const nodes={title:{},content:{}}; let observed=null;
        const c={URLSearchParams,localStorage:{getItem:()=>null},
            location:{pathname:'/success.html',search:'?order=TABO-SYNTHETIC',hash:''},
            history:{state:null,replaceState(value,unused,url){this.state=value;c.location.search='';assert.equal(url,'/success.html');}},
            document:{documentElement:{},getElementById:id=>nodes[id],querySelectorAll:()=>[]},
            fetch:async()=>({ok,json:async()=>({books:[{id:'book20',name:'Synthetic book',remaining:2}],snapPurchase:snap})}),
            TaboSnapTracking:{purchase(data){if(snapThrows)throw Error('blocked');assert.equal(c.location.search,'');observed=data;}}};
        c.window=c; vm.createContext(c); vm.runInContext(receiptScript,c); await flush();
        return {observed,html:nodes.content.innerHTML,state:c.history.state};
    }
    assert.equal((await receiptDocument({ok:false})).observed,null);
    assert.equal((await receiptDocument({snap:null})).observed,null);
    const paid=await receiptDocument(); assert.equal(paid.observed.transaction_id,payload.transaction_id);
    assert(paid.html.includes('Synthetic book')); assert.equal(paid.state.taboOrder,'TABO-SYNTHETIC');
    assert((await receiptDocument({snapThrows:true})).html.includes('Synthetic book'));
    console.log('Snap: paid-only receipts, actual charges, device URLs, deduplication, no buyer fields and independent Meta delivery passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
