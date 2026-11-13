/* Execute actual storefront pricing and product rendering without external services. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rendered = require('./rendered_template.cjs');
const html = rendered('index.html');
const booksSource = html.match(/const BOOKS = (\[[^\n]*\]);/)[1];
const source = fs.readFileSync('checkout-core.js','utf8').split('/* Catalog pricing start */')[1].split('/* Catalog pricing end */')[0];
const egp = Object.fromEntries(Array.from({length: 19}, (_, i) => [String(i + 1), 199 + i * 100]));
const usd = {1: 9.99, 2: 17.99, 3: 24.99, ...Object.fromEntries(Array.from({length: 16}, (_, i) => [String(i + 4), Number((29.99 + i * 5).toFixed(2))]))};
const context = vm.createContext({storeConfig: {}, PRICING_LADDER: {}, currentLanguage: 'ar', cart: []});
vm.runInContext('const BOOKS=' + booksSource + '; BOOKS.forEach((book,i)=>book.id="book"+(i+1));\n' + source, context);
for (const [currency, ladder, fixed, compare] of [['EGP', egp, 500, 299], ['USD', usd, 19.99, 14.99]]) {
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

function product(bookId, currency, promotion) {
    const detail = rendered('book.html','/books/'+bookId);
    assert(detail.indexOf('id="bookPromotionDetail"') < detail.indexOf('class="cover-wrap"'));
    const detailScript = detail.slice(detail.indexOf('const BOOKS='), detail.indexOf('async function submitRating('));
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {textContent: '', hidden: false, style: {}, setAttribute() {}, removeAttribute(name) {delete this[name];}});
        return nodes.get(id);
    };
    const c = vm.createContext({location: {pathname: '/books/' + bookId, search: ''}, URLSearchParams,
        localStorage: {getItem: () => null}, navigator: {language: 'ar'}, performance: {now: () => 0},
        window:{}, document: {documentElement: {}, getElementById: node, querySelectorAll: () => []}});
    vm.runInContext(detailScript, c);
    c.productConfig = {currency, prices: {'1': currency === 'EGP' ? 199 : 9.99}, compareAtPrice: currency === 'EGP' ? 299 : 14.99, countries: [], bookPromotion: promotion};
    vm.runInContext('config=productConfig;promotionDeadline=config.bookPromotion?.expiresInMs||0;pricingLoaded=true;render()', c);
    return nodes;
}
for (const currency of ['EGP', 'USD']) {
    const nodes = product('book20', currency);
    assert.equal(nodes.get('previewLink').hidden, true);
    assert.equal(nodes.get('previewLink').href, undefined);
    assert.equal(nodes.get('previousPrice').hidden, true);
    assert.equal(nodes.has('fixedPriceNote'), false);
    assert.equal(nodes.has('downloads'), false);
    assert.match(nodes.get('price').textContent, currency === 'EGP' ? /500/ : /19\.99/);
    assert.equal(nodes.get('price').textContent, nodes.get('mobileBuyPrice').textContent);
    const companion = product('book21', currency);
    assert.equal(companion.get('previewLink').hidden, true);
    assert.equal(companion.get('previewLink').href, undefined);
    assert.equal(companion.get('previousPrice').hidden, true);
    assert.match(companion.get('price').textContent, currency === 'EGP' ? /500/ : /19\.99/);
    assert.equal(companion.get('price').textContent, companion.get('mobileBuyPrice').textContent);
    const court = product('book22', currency);
    assert.equal(court.get('previewLink').hidden, true);
    assert.equal(court.get('previewLink').href, undefined);
    assert.equal(court.get('previousPrice').hidden, true);
    assert.equal(court.has('fixedPriceNote'), false);
    assert.equal(court.has('downloads'), false);
    assert.match(court.get('price').textContent, currency === 'EGP' ? /500/ : /19\.99/);
    assert.equal(court.get('price').textContent, court.get('mobileBuyPrice').textContent);
    const hormones = product('book23', currency);
    assert.equal(hormones.get('previewLink').hidden, true);
    assert.equal(hormones.get('previewLink').href, undefined);
    assert.equal(hormones.get('previousPrice').hidden, true);
    assert.equal(hormones.has('fixedPriceNote'), false);
    assert.equal(hormones.has('downloads'), false);
    assert.match(hormones.get('price').textContent, currency === 'EGP' ? /500/ : /19\.99/);
    assert.equal(hormones.get('price').textContent, hormones.get('mobileBuyPrice').textContent);
    const microscope = product('book24', currency);
    assert.equal(microscope.get('previewLink').hidden, true);
    assert.equal(microscope.get('previewLink').href, undefined);
    assert.equal(microscope.get('previousPrice').hidden, true);
    assert.equal(microscope.has('fixedPriceNote'), false);
    assert.equal(microscope.has('downloads'), false);
    assert.match(microscope.get('price').textContent, currency === 'EGP' ? /500/ : /19\.99/);
    assert.equal(microscope.get('price').textContent, microscope.get('mobileBuyPrice').textContent);
    const ordinary = product('book19', currency);
    assert.equal(ordinary.get('previewLink').hidden, false);
    assert.equal(ordinary.get('previewLink').href, '/previews/book19');
    assert.equal(ordinary.get('previousPrice').hidden, false);
}
const promotion = {bookId: 'book21', currency: 'USD', price: 10, regularPrice: 19.99, percent: 50, expiresInMs: 604800000};
const discounted = product('book21', 'USD', promotion);
assert.equal(discounted.get('price').textContent, '$10.00');
assert.equal(discounted.get('previousPrice').textContent, '$19.99');
assert.equal(discounted.get('mobileBuyPrice').textContent, '$10.00');
assert.equal(discounted.get('bookPromotionDetail').hidden, false);
assert.equal(discounted.get('bookPromotion').hidden, true);
assert.match(discounted.get('bookPromotionHeadline').textContent, /٥٠/);
assert.equal(discounted.get('bookPromotionPrice').textContent, '$10.00');
assert.equal(discounted.get('bookPromotionRegular').textContent, '$19.99');
assert.equal(discounted.get('bookPromotionDays').textContent, '7 أيام');
assert.equal(discounted.get('bookPromotionCountdown').textContent, '00:00:00');
const egypt = product('book21', 'EGP', promotion);
assert.equal(egypt.get('price').textContent, '500 جنيه');
assert.equal(egypt.get('bookPromotionDetail').hidden, true);
assert.equal(product('book20', 'USD', promotion).get('bookPromotionDetail').hidden, true);
assert.equal(product('book21', 'USD').get('bookPromotionDetail').hidden, true);
console.log('Catalog pricing: book prices, preview rules and the targeted USD sale display passed.');
