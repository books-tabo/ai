/* Execute actual storefront pricing and product rendering without external services. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rendered = require('./rendered_template.cjs');
const html = rendered('index.html');
const booksSource = html.match(/const BOOKS = (\[[^\n]*\]);/)[1];
const source = fs.readFileSync('checkout-core.js','utf8').split('/* Catalog pricing start */')[1].split('/* Catalog pricing end */')[0];
const egp=Object.fromEntries(Array.from({length:25},(_,i)=>[i+1,Number((149.99*(i+1)-i).toFixed(2))]));
const usd=Object.fromEntries(Array.from({length:25},(_,i)=>[i+1,Number((4.99*(i+1)-i).toFixed(2))]));
const context=vm.createContext({storeConfig:{},PRICING_LADDER:{},currentLanguage:'ar',cart:[]});
vm.runInContext('const BOOKS='+booksSource+';'+source,context);
for(const [currency,ladder,unit] of [['EGP',egp,149.99],['USD',usd,4.99]]){
 context.storeConfig={currency,prices:ladder,compareAtPrice:unit};context.PRICING_LADDER=ladder;
 for(let count=1;count<=25;count++){
  context.cart=Array.from({length:count},(_,i)=>({id:'book'+(i+1)}));
  const result=vm.runInContext('calculatePricing()',context);
  assert.equal(result.count,count);assert.equal(result.standardCount,count);assert.equal(result.fixedTotal,0);
  assert.equal(result.final,ladder[count]);assert.equal(result.discount,count-1);
  context.cart.reverse();assert.equal(vm.runInContext('calculatePricing().final',context),ladder[count]);
 }
 assert.equal(vm.runInContext('bookPolicy(BOOKS[19]).previewEnabled',context),false);
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
    c.productConfig = {currency, prices: {'1': currency === 'EGP' ? 149.99 : 4.99}, compareAtPrice: currency === 'EGP' ? 149.99 : 4.99, countries: [], bookPromotion: promotion};
    vm.runInContext('config=productConfig;promotionDeadline=config.bookPromotion?.expiresInMs||0;pricingLoaded=true;render()', c);
    return nodes;
}
for(const currency of ['EGP','USD']){
 for(let n=1;n<=25;n++){
  const id='book'+n,nodes=product(id,currency);
  assert.equal(nodes.get('price').textContent,currency==='EGP'?'149.99 جنيه':'$4.99');
  assert.equal(nodes.get('mobileBuyPrice').textContent,nodes.get('price').textContent);
  assert.equal(nodes.get('previousPrice').hidden,true);
  assert.equal(nodes.get('bookPromotionDetail').hidden,true);
  assert.equal(nodes.get('previewLink').hidden,n>=20&&n<=24);
  if(n<20||n===25)assert.equal(nodes.get('previewLink').href,'/previews/'+id);
 }
}
const promotion = {bookId: 'book21', currency: 'USD', price: 2.49, regularPrice: 4.99, percent: 50, expiresInMs: 604800000};
const discounted = product('book21', 'USD', promotion);
assert.equal(discounted.get('price').textContent, '$2.49');
assert.equal(discounted.get('previousPrice').textContent, '$4.99');
assert.equal(discounted.get('mobileBuyPrice').textContent, '$2.49');
assert.equal(discounted.get('bookPromotionDetail').hidden, false);
assert.equal(discounted.get('bookPromotion').hidden, true);
assert.match(discounted.get('bookPromotionHeadline').textContent, /٥٠/);
assert.equal(discounted.get('bookPromotionPrice').textContent, '$2.49');
assert.equal(discounted.get('bookPromotionRegular').textContent, '$4.99');
assert.equal(discounted.get('bookPromotionDays').textContent, '7 أيام');
assert.equal(discounted.get('bookPromotionCountdown').textContent, '00:00:00');
const egypt = product('book21', 'EGP', promotion);
assert.equal(egypt.get('price').textContent, '149.99 جنيه');
assert.equal(egypt.get('bookPromotionDetail').hidden, true);
const egyptPromotion = {...promotion, currency:'EGP', price:74.99, regularPrice:149.99};
const egyptDiscounted = product('book21', 'EGP', egyptPromotion);
assert.equal(egyptDiscounted.get('price').textContent, '74.99 جنيه');
assert.equal(egyptDiscounted.get('mobileBuyPrice').textContent, '74.99 جنيه');
assert.equal(egyptDiscounted.get('previousPrice').textContent, '149.99 جنيه');
assert.equal(egyptDiscounted.get('mobilePreviousPrice').textContent, '149.99 جنيه');
assert.equal(egyptDiscounted.get('bookPromotionDetail').hidden, false);
assert.equal(egyptDiscounted.get('bookPromotionPrice').textContent, '74.99 جنيه');
assert.equal(egyptDiscounted.get('bookPromotionRegular').textContent, '149.99 جنيه');
assert.equal(egyptDiscounted.get('bookPromotionDays').textContent, '7 أيام');
assert.equal(egyptDiscounted.get('bookPromotionCountdown').textContent, '00:00:00');
assert.equal(product('book20', 'EGP', egyptPromotion).get('bookPromotionDetail').hidden, true);
assert.equal(product('book20', 'USD', promotion).get('bookPromotionDetail').hidden, true);
assert.equal(product('book21', 'USD').get('bookPromotionDetail').hidden, true);
console.log('Catalog pricing: book prices, preview rules and the targeted USD sale display passed.');
