// Isolated JavaScript runtime tests: the external Pixel is stubbed, never requested.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'meta-tracking.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = {eventID: 'a'.repeat(64), value: 299, currency: 'EGP', content_ids: ['book1', 'book18']};

function runtime({hostname='tabobooks.com', pathname='/', search='', storage=new Map(), fail=false}={}) {
    const appended = [], calls = [];
    const document = {
        currentScript: {dataset: {pixelId: '1371060818078535'}, nonce: 'synthetic-nonce'},
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
    console.log('Meta tracking: 4 isolated scenarios passed (events, deduplication, blocked delivery, authenticated receipt).');
})().catch(error => {console.error(error); process.exitCode = 1;});
