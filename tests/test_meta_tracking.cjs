// Isolated JavaScript runtime tests: the external Pixel is stubbed, never requested.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'meta-tracking.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = {eventID: 'a'.repeat(64), value: 299, currency: 'EGP', content_ids: ['book1', 'book18']};

function runtime({hostname='tabobooks.com', pathname='/', search='', storage=new Map(), fail=false, additionalPixelId}={}) {
    const appended = [], calls = [];
    const document = {
        currentScript: {dataset: {pixelId: '1371060818078535', additionalPixelId}, nonce: 'synthetic-nonce'},
        createElement: () => ({}),
        head: {appendChild: script => {appended.push(script); queueMicrotask(() => fail ? script.onerror() : script.onload());}}
    };
    const context = {document, location: {hostname, pathname, search, hash: ''},
        localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
        fbq: (...args) => calls.push(args)};
    context.window = context;
    vm.createContext(context); vm.runInContext(source, context);
    return {context, calls, appended, storage};
}

(async () => {
    const home = runtime(); await flush();
    assert.equal(home.appended.length, 1);
    assert.equal(home.appended[0].nonce, 'synthetic-nonce');
    assert(home.calls.some(args => args[0] === 'set' && args[1] === 'autoConfig' && args[2] === false));
    assert(home.calls.some(args => args[2] === 'PageView'));
    await home.context.TaboTracking.track('AddToCart', {...payload, email: 'synthetic@example.invalid'});
    assert(!JSON.stringify(home.calls).includes('synthetic@example.invalid'));
    await home.context.TaboTracking.track('ViewContent', {content_ids:['book20'],value:15.9,currency:'USD'});
    assert(home.calls.some(args=>args[2]==='ViewContent'&&args[3].content_ids[0]==='book20'&&args[3].value===15.9));
    await home.context.TaboTracking.track('Purchase', payload);
    await home.context.TaboTracking.purchase(payload);
    assert(!home.calls.some(args => args[2] === 'Purchase'));
    const stage = runtime({hostname: 'tabo-books-preview.onrender.com'});
    await flush(); assert.equal(stage.appended.length, 0);

    const receipt = runtime({pathname: '/success.html', search: '?order=synthetic'});
    await receipt.context.TaboTracking.purchase(payload);
    assert.equal(receipt.appended.length, 0); // Never load Pixel with protected URL parameters.
    receipt.context.location.search = '';
    await Promise.all([receipt.context.TaboTracking.purchase(payload), receipt.context.TaboTracking.purchase(payload)]);
    assert.equal(receipt.calls.filter(args => args[2] === 'Purchase').length, 1);
    assert.equal(receipt.calls.find(args => args[2] === 'Purchase')[4].eventID, payload.eventID);
    assert(!receipt.calls.some(args => args[2] === 'PageView'));
    const refreshed = runtime({pathname: '/success.html', storage: receipt.storage});
    await refreshed.context.TaboTracking.purchase(payload);
    assert.equal(refreshed.appended.length, 0);
    const blocked = runtime({pathname: '/success.html', fail: true});
    await blocked.context.TaboTracking.purchase(payload);
    assert.equal(blocked.storage.size, 0);

    const extraId = '1390132816288252';
    const dualHome = runtime({additionalPixelId: extraId}); await flush();
    assert.equal(dualHome.appended.length, 1);
    assert.deepEqual(dualHome.calls.filter(args => args[2] === 'PageView').map(args => args[1]).sort(),
        ['1371060818078535', extraId].sort());
    // Kuwait's storefront uses KWD; preserve the displayed value and currency on both pixels.
    const kwdProduct = {content_ids:['book21'], value:6.2, currency:'KWD'};
    for (const event of ['ViewContent', 'AddToCart', 'InitiateCheckout']) {
        await dualHome.context.TaboTracking.track(event, kwdProduct);
        const calls = dualHome.calls.filter(args => args[2] === event);
        assert.equal(calls.length, 2, `${event} must reach both configured pixels for Kuwait`);
        assert(calls.every(args => args[3].currency === 'KWD' && args[3].value === 6.2));
    }
    await dualHome.context.TaboTracking.track('AddToCart', {...kwdProduct, currency:'XXX'});
    assert.equal(dualHome.calls.filter(args => args[2] === 'AddToCart').length, 2);
    const kwdReceipt = runtime({pathname:'/success.html', additionalPixelId:extraId});
    const kwdPurchase = {...kwdProduct, eventID:'b'.repeat(64), additional_pixel_ids:[extraId]};
    await Promise.all([kwdReceipt.context.TaboTracking.purchase(kwdPurchase), kwdReceipt.context.TaboTracking.purchase(kwdPurchase)]);
    assert.equal(kwdReceipt.calls.filter(args => args[2] === 'Purchase').length, 2);
    assert(kwdReceipt.calls.filter(args => args[2] === 'Purchase').every(args => args[3].currency === 'KWD' && args[3].value === 6.2));
    const dualReceipt = runtime({pathname: '/success.html', additionalPixelId: extraId});
    await dualReceipt.context.TaboTracking.purchase(payload);
    assert.equal(dualReceipt.calls.filter(args => args[2] === 'Purchase' && args[1] === extraId).length, 0);
    const eligible = {...payload, additional_pixel_ids: [extraId, '999999999']};
    await Promise.all([dualReceipt.context.TaboTracking.purchase(eligible), dualReceipt.context.TaboTracking.purchase(eligible)]);
    assert.equal(dualReceipt.calls.filter(args => args[2] === 'Purchase').length, 2);
    assert.equal(dualReceipt.calls.filter(args => args[2] === 'Purchase' && args[1] === extraId).length, 1);
    assert(!dualReceipt.calls.some(args => args[1] === '999999999'));
    const dualRefreshed = runtime({pathname: '/success.html', additionalPixelId: extraId, storage: dualReceipt.storage});
    await dualRefreshed.context.TaboTracking.purchase(eligible);
    assert.equal(dualRefreshed.appended.length, 0);

    // Execute the actual receipt document, including authorization and clean-URL ordering.
    const html = fs.readFileSync(path.join(__dirname, '..', 'success.html'), 'utf8');
    const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
    async function receiptDocument(ok, state = null) {
        let observed = null;
        const nodes = {title: {}, content: {}};
        const c = {URLSearchParams, localStorage: {getItem: () => null},
            location: {search: state ? '' : '?order=TABO-SYNTHETIC', pathname: '/success.html'},
            history: {state, replaceState(value, unused, url) {this.state = value; c.location.search = ''; assert.equal(url, '/success.html');}},
            document: {documentElement: {}, getElementById: id => nodes[id], querySelectorAll: () => []},
            fetch: async url => ({ok, json: async () => ({books: [], metaPurchase: payload})}),
            TaboTracking: {purchase: data => {assert.equal(c.location.search, ''); observed = data;}}};
        c.window = c; vm.createContext(c); vm.runInContext(script, c); await flush();
        return {observed, state: c.history.state};
    }
    assert.equal((await receiptDocument(false)).observed, null);
    const authorized = await receiptDocument(true);
    assert.equal(authorized.observed.eventID, payload.eventID);
    assert.equal((await receiptDocument(true, authorized.state)).observed.eventID, payload.eventID);
    console.log('Meta tracking: isolated scenarios passed (events, KWD, dual pixels, deduplication, blocked delivery, authenticated receipt).');
})().catch(error => {console.error(error); process.exitCode = 1;});
