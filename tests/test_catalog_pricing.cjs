/* Execute actual storefront pricing and product rendering without external services. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rendered = require('./rendered_template.cjs');
const html = rendered('index.html');
const booksSource = html.match(/const BOOKS = (\[[^\n]*\]);/)[1];
const source = html.split('/* Catalog pricing start */')[1].split('/* Catalog pricing end */')[0];
const egp = Object.fromEntries(Array.from({length: 19}, (_, i) => [String(i + 1), 199 + i * 100]));
const usd = {1: 9.99, 2: 17.99, 3: 24.99, ...Object.fromEntries(Array.from({length: 16}, (_, i) => [String(i + 4), Number((29.99 + i * 5).toFixed(2))]))};
const context = vm.createContext({storeConfig: {}, PRICING_LADDER: {}, currentLanguage: 'ar', cart: []});
vm.runInContext('const BOOKS=' + booksSource + '; BOOKS.forEach((book,i)=>book.id="book"+(i+1));\n' + source, context);
for (const [currency, ladder, fixed, compare] of [['EGP', egp, 500, 299], ['USD', usd, 15.9, 14.99]]) {
    context.storeConfig = {currency, prices: ladder, compareAtPrice: compare};
    context.PRICING_LADDER = ladder;
    for (let count = 0; count <= 19; count++) {
        const ordinary = Array.from({length: count}, (_, i) => ({id: 'book' + (i + 1)}));
        context.cart = [...ordinary, {id: 'book20'}];
        const result = vm.runInContext('calculatePricing()', context);
        assert.equal(result.standardCount, count);
        assert.equal(result.fixedTotal, fixed);
        assert.equal(result.final, Math.round(((ladder[count] || 0) + fixed) * 100) / 100);
        context.cart.reverse();
        assert.equal(vm.runInContext('calculatePricing().final', context), result.final);
        context.cart = ordinary;
        assert.equal(vm.runInContext('calculatePricing().final', context), ladder[count] || 0);
    }
    context.cart = [{id: 'book20'}];
    assert.equal(vm.runInContext('calculatePricing().discount', context), 0);
    assert.equal(vm.runInContext('bookPolicy(BOOKS[19]).previewEnabled', context), false);
}

function product(bookId, currency) {
    const detail = rendered('book.html','/books/'+bookId);
    const detailScript = detail.slice(detail.indexOf('const BOOKS='), detail.indexOf('async function submitRating('));
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {textContent: '', hidden: false, style: {}, setAttribute() {}, removeAttribute(name) {delete this[name];}});
        return nodes.get(id);
    };
    const c = vm.createContext({location: {pathname: '/books/' + bookId, search: ''}, URLSearchParams,
        localStorage: {getItem: () => null}, navigator: {language: 'ar'},
        window:{}, document: {documentElement: {}, getElementById: node, querySelectorAll: () => []}});
    vm.runInContext(detailScript, c);
    c.productConfig = {currency, prices: {'1': currency === 'EGP' ? 199 : 9.99}, compareAtPrice: currency === 'EGP' ? 299 : 14.99, countries: []};
    vm.runInContext('config=productConfig;pricingLoaded=true;render()', c);
    return nodes;
}
for (const currency of ['EGP', 'USD']) {
    const nodes = product('book20', currency);
    assert.equal(nodes.get('previewLink').hidden, true);
    assert.equal(nodes.get('previewLink').href, undefined);
    assert.equal(nodes.get('previousPrice').hidden, true);
    assert.equal(nodes.get('fixedPriceNote').hidden, false);
    assert.match(nodes.get('price').textContent, currency === 'EGP' ? /500/ : /15\.90/);
    assert.equal(nodes.get('price').textContent, nodes.get('mobileBuyPrice').textContent);
    const companion = product('book21', currency);
    assert.equal(companion.get('previewLink').hidden, true);
    assert.equal(companion.get('previewLink').href, undefined);
    assert.equal(companion.get('previousPrice').hidden, true);
    assert.match(companion.get('price').textContent, currency === 'EGP' ? /500/ : /15\.99/);
    assert.equal(companion.get('price').textContent, companion.get('mobileBuyPrice').textContent);
    const ordinary = product('book19', currency);
    assert.equal(ordinary.get('previewLink').hidden, false);
    assert.equal(ordinary.get('previewLink').href, '/previews/book19');
    assert.equal(ordinary.get('previousPrice').hidden, false);
}
console.log('Catalog pricing: all 19 bundle sizes, fixed Sigma totals, order/removal, both currencies, product/mobile prices and preview visibility passed.');
