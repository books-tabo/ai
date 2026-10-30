// Stub Google completely: no advertising requests or synthetic live conversions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'google-ads-tracking.js'), 'utf8');
const receiptScript = fs.readFileSync(path.join(__dirname, '..', 'success.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = {transaction_id: 'a'.repeat(64), value: 829.27, currency: 'EGP'};
const destination = 'AW-18456253761/Z30jCMCguqYdEMGq0OBE';

function runtime({url='https://tabobooks.com/', storage=new Map(), blockedStorage=false,
    fail=false, callback=true, referrer='https://tabobooks.com/pending.html?key=synthetic-secret'}={}) {
    const calls = [], appended = [];
    const context = {URL, URLSearchParams, location: new URL(url),
        document: {currentScript: {dataset: {googleTagId: 'AW-18456253761', googleConversionLabel: 'Z30jCMCguqYdEMGq0OBE'}, nonce: 'test-nonce'},
            referrer, createElement: () => ({}), head: {appendChild(script) {
                appended.push(script); queueMicrotask(() => fail ? script.onerror() : script.onload());
            }}},
        localStorage: {getItem(key) {if (blockedStorage) throw Error('Disabled'); return storage.get(key);},
            setItem(key, value) {if (blockedStorage) throw Error('Disabled'); storage.set(key, value);}},
        gtag(...args) {calls.push(args); if (callback && args[0]==='event') args[2].event_callback();}};
    context.window = context; vm.createContext(context); vm.runInContext(source, context);
    return {context, calls, appended, storage};
}

const conversions = runtime => runtime.calls.filter(x => x[0]==='event' && x[1]==='conversion');

(async () => {
    const adUrl = 'https://tabobooks.com/books/book20?gclid=synthetic-click&utm_source=google&fbclid=synthetic-meta&email=private@example.invalid#token';
    const landing = runtime({url: adUrl}); await flush();
    assert.equal(landing.appended.length, 1);
    assert.equal(landing.appended[0].src, 'https://www.googletagmanager.com/gtag/js?id=AW-18456253761');
    assert.equal(landing.appended[0].nonce, 'test-nonce');
    const config = landing.calls.find(x => x[0]==='config');
    assert.equal(config[1], 'AW-18456253761');
    assert(config[2].page_location.includes('gclid=synthetic-click'));
    assert(config[2].page_location.includes('utm_source=google'));
    assert(config[2].page_location.includes('fbclid=synthetic-meta'));
    assert(!config[2].page_location.includes('private@'));
    assert(!config[2].page_location.includes('email='));
    assert.equal(config[2].page_referrer, 'https://tabobooks.com/pending.html');
    assert.equal(landing.context.location.href, adUrl);
    await landing.context.TaboGoogleAds.purchase(payload);
    assert.equal(conversions(landing).length, 0);
    vm.runInContext(source, landing.context); await flush();
    assert.equal(landing.appended.length, 1);

    const preview = runtime({url:'https://tabo-offers-review.onrender.com/success.html'});
    assert.equal(preview.context.TaboGoogleAds, undefined);
    assert.equal(preview.appended.length, 0);
    const receipt = runtime({url:'https://tabobooks.com/success.html?order=private#secret'});
    assert.equal(receipt.appended.length, 0);
    await receipt.context.TaboGoogleAds.purchase(payload);
    assert.equal(receipt.appended.length, 0);
    receipt.context.location.search = ''; receipt.context.location.hash = '';
    for (const bad of [{...payload, transaction_id:''}, {...payload, value:0}, {...payload, value:'1'},
        {...payload, value:NaN}, {...payload, currency:'XXX'}, null]) {
        await receipt.context.TaboGoogleAds.purchase(bad);
    }
    assert.equal(receipt.appended.length, 0);
    await Promise.all([receipt.context.TaboGoogleAds.purchase({...payload, email:'private@example.invalid', send_to:'WRONG'}),
        receipt.context.TaboGoogleAds.purchase(payload)]);
    assert.equal(conversions(receipt).length, 1);
    const event = conversions(receipt)[0][2];
    assert.equal(event.send_to, destination);
    assert.equal(event.value, 829.27); assert.equal(event.currency, 'EGP');
    assert.equal(event.transaction_id, payload.transaction_id);
    assert.equal(event.page_location, 'https://tabobooks.com/success.html');
    assert(!JSON.stringify(receipt.calls).includes('synthetic-secret'));
    assert(!JSON.stringify(receipt.calls).includes('private@example.invalid'));
    const refreshed = runtime({url:'https://tabobooks.com/success.html', storage: receipt.storage});
    await refreshed.context.TaboGoogleAds.purchase(payload);
    assert.equal(refreshed.appended.length, 0);
    await refreshed.context.TaboGoogleAds.purchase({...payload, transaction_id:'b'.repeat(64), value:23, currency:'USD'});
    assert.equal(conversions(refreshed)[0][2].currency, 'USD');

    const blocked = runtime({url:'https://tabobooks.com/success.html', fail:true});
    await blocked.context.TaboGoogleAds.purchase(payload);
    assert.equal(conversions(blocked).length, 0); assert.equal(blocked.storage.size, 0);
    const noStorage = runtime({url:'https://tabobooks.com/success.html', blockedStorage:true});
    await noStorage.context.TaboGoogleAds.purchase(payload);
    await noStorage.context.TaboGoogleAds.purchase(payload);
    assert.equal(conversions(noStorage).length, 1);
    const pendingCallback = runtime({url:'https://tabobooks.com/success.html', callback:false});
    await pendingCallback.context.TaboGoogleAds.purchase(payload);
    await pendingCallback.context.TaboGoogleAds.purchase(payload);
    assert.equal(conversions(pendingCallback).length, 1); assert.equal(pendingCallback.storage.size, 0);
    conversions(pendingCallback)[0][2].event_callback();
    assert.equal(pendingCallback.storage.size, 1);

    // Execute the actual receipt page. Neither tracker is trusted before a successful API response.
    async function receiptDocument({ok=true, google=payload, meta=null, metaThrows=false, googleThrows=false, state=null}={}) {
        let googleObserved = null, metaObserved = null;
        const nodes = {title:{}, content:{}};
        const c = {URLSearchParams, localStorage:{getItem:()=>null},
            location:{pathname:'/success.html', search:state?'':'?order=TABO-SYNTHETIC', hash:''},
            history:{state, replaceState(value,unused,url) {this.state=value; c.location.search=''; assert.equal(url,'/success.html');}},
            document:{documentElement:{}, getElementById:id=>nodes[id], querySelectorAll:()=>[]},
            fetch:async()=>({ok, json:async()=>({books:[{id:'book1', name:'كتاب اختباري', remaining:2}], googleAdsPurchase:google, metaPurchase:meta})}),
            TaboTracking:{purchase(data) {if(metaThrows)throw Error('Meta blocked'); assert.equal(c.location.search,''); metaObserved=data;}},
            TaboGoogleAds:{purchase(data) {if(googleThrows)throw Error('Google blocked'); assert.equal(c.location.search,''); googleObserved=data;}}};
        c.window=c; vm.createContext(c); vm.runInContext(receiptScript,c); await flush();
        return {googleObserved, metaObserved, state:c.history.state, html:nodes.content.innerHTML};
    }
    assert.equal((await receiptDocument({ok:false})).googleObserved,null);
    assert.equal((await receiptDocument({google:null})).googleObserved,null);
    const paid = await receiptDocument();
    assert.equal(paid.googleObserved.transaction_id,payload.transaction_id);
    assert(paid.html.includes('كتاب اختباري'));
    assert.equal((await receiptDocument({state:paid.state})).googleObserved.transaction_id,payload.transaction_id);
    const fakeMeta = {eventID:'c'.repeat(64)};
    assert.equal((await receiptDocument({meta:fakeMeta,metaThrows:true})).googleObserved.transaction_id,payload.transaction_id);
    const googleBlocked = await receiptDocument({meta:fakeMeta,googleThrows:true});
    assert.equal(googleBlocked.metaObserved.eventID,fakeMeta.eventID);
    assert(googleBlocked.html.includes('كتاب اختباري'));
    console.log('Google Ads: attribution, paid receipt, dynamic charge, deduplication, blockers, privacy and independent Meta delivery passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
