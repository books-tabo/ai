/* Actual shared pricing + storefront actions, never external events or payment. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const rendered=require('./rendered_template.cjs');
const html=rendered('index.html');
const pricing=fs.readFileSync('checkout-core.js','utf8').split('/* Catalog pricing start */')[1].split('/* Catalog pricing end */')[0];
const actions=html.split('/* Curated offer actions start */')[1].split('/* Curated offer actions end */')[0];
const books=html.match(/const BOOKS = (\[[^\n]*\]);/)[1];
const engine=fs.readFileSync('offer-pricing.js','utf8');
assert(html.includes(engine));
const fixture=JSON.parse(execFileSync('python3',['-c',`
import json,itertools,catalog,localization
members=sorted(set(b for o in catalog.OFFERS.values() for b in o['books']))
result=[]
for country in ('EG','KW'):
 c=localization.pricing_for_country(catalog.PRICES,country);cases=[]
 for mask in range(1<<len(members)):
  selected=[b for i,b in enumerate(members) if mask&(1<<i)]
  for extra in ([],['book1']):
   ids=selected+extra;cases.append({'ids':ids,'total':catalog.total_for_items(ids,c)})
 result.append({'config':c,'cases':cases})
print(json.dumps(result))
`],{encoding:'utf8',maxBuffer:4*1024*1024}));
const events=[],saved=[],opened=[],histories=[];
const c=vm.createContext({storeConfig:{},PRICING_LADDER:{},currentLanguage:'ar',cart:[],
  window:{TaboTracking:{track:(name,data)=>events.push({name,data})}},URLSearchParams,
  location:{pathname:'/',search:'',hash:''},history:{state:null,replaceState:(state,unused,url)=>histories.push(url)},
  document:{querySelectorAll:()=>[]},updateCart:()=>{},openCart:()=>opened.push(true),
  saveCart:()=>saved.push(JSON.parse(vm.runInContext('JSON.stringify(cart)',c)))});
vm.runInContext(engine+'\nconst BOOKS='+books+';\n'+pricing+'\n'+actions,c);
let count=0;
for(const group of fixture){
 c.storeConfig=group.config;c.PRICING_LADDER=group.config.prices;
 for(const sample of group.cases){
  c.cart=sample.ids.map(id=>({type:'book',id}));
  assert.equal(vm.runInContext('calculatePricing().final',c),sample.total,group.config.currency+':'+sample.ids);
  count++;
 }
}
c.storeConfig=fixture[1].config;c.PRICING_LADDER=c.storeConfig.prices;c.cart=[];
assert.equal(c.addCuratedOffer('unknown'),false);assert.equal(events.length,0);
assert(c.addCuratedOffer('change-starter'));assert.equal(c.cart.length,3);
assert.equal(vm.runInContext('calculatePricing().final',c),15);
assert.equal(events.length,1);assert.equal(events[0].data.value,15);
c.addCuratedOffer('change-starter');assert.equal(c.cart.length,3);assert.equal(events.length,1);
c.addCuratedOffer('self-growth');assert.equal(c.cart.length,5);assert.equal(events[1].data.value,8);
assert.equal(vm.runInContext('calculatePricing().final',c),23);
c.cart.push({id:'book20'});assert.equal(vm.runInContext('calculatePricing().final',c),38.9);
c.storeConfig=fixture[0].config;c.PRICING_LADDER=c.storeConfig.prices;
assert.equal(vm.runInContext('calculatePricing().final',c),900);
c.cart=c.cart.filter(x=>x.id!=='book19');assert.equal(vm.runInContext('calculatePricing().final',c),999);
assert(saved.every(items=>new Set(items.map(x=>x.id)).size===items.length));
c.cart=[];c.storeConfig=fixture[1].config;c.PRICING_LADDER=c.storeConfig.prices;
c.location.search='?offer=money-and-wealth&utm_source=facebook&utm_campaign=wealth&fbclid=synthetic%2Btest&cart=1';
c.completeOfferIntent();assert.equal(c.cart.length,4);assert.equal(vm.runInContext('calculatePricing().final',c),20);
c.cart=[];c.storeConfig=fixture[1].config;c.PRICING_LADDER=c.storeConfig.prices;
c.addCuratedOffer('sigma-couple');assert.equal(c.cart.length,2);assert.equal(vm.runInContext('calculatePricing().final',c),20);
assert.equal(events.at(-1).data.value,20);
c.addCuratedOffer('sigma-couple');assert.equal(c.cart.length,2);
c.cart=c.cart.filter(x=>x.id!=='book20');assert.equal(vm.runInContext('calculatePricing().final',c),15.99);
c.cart=[{id:'book20'}];assert.equal(vm.runInContext('calculatePricing().final',c),15.9);
c.addCuratedOffer('sigma-couple');assert.equal(events.at(-1).data.value,4.1);
assert.equal(vm.runInContext('calculatePricing().final',c),20);
c.addCuratedOffer('change-starter');assert.equal(vm.runInContext('calculatePricing().final',c),35);
c.storeConfig=fixture[0].config;c.PRICING_LADDER=c.storeConfig.prices;c.cart=[];
assert.equal(c.addCuratedOffer('sigma-couple'),false);assert.equal(c.cart.length,0);
c.cart=[{id:'book20'},{id:'book21'}];assert.equal(vm.runInContext('calculatePricing().final',c),1000);
const after=new URLSearchParams(histories.at(-1).split('?')[1]);
assert.equal(after.get('utm_source'),'facebook');assert.equal(after.get('fbclid'),'synthetic+test');assert.equal(after.has('offer'),false);
vm.runInContext(fs.readFileSync('offer-ui.js','utf8'),c);
const destination=c.TaboOfferUI.cartUrl('self-growth','?utm_source=instagram&fbclid=synthetic%2Fid');
const params=new URLSearchParams(destination.split('?')[1]);assert.equal(params.get('fbclid'),'synthetic/id');assert.equal(params.get('offer'),'self-growth');
// The USD-only exception must never advertise a discounted EGP cart action or
// compare the pair with the unrelated two-ordinary-book quantity ladder.
const elements=new Map();
const element=key=>{if(!elements.has(key))elements.set(key,{textContent:'',hidden:false,setAttribute(name,value){this[name]=value;}});return elements.get(key);};
const buy=element('buy');
const card={dataset:{offerId:'sigma-couple'},querySelector:element,querySelectorAll:selector=>selector==='[data-offer-buy]'||selector==='[data-offer-buy],[data-add-offer]'?[buy]:[]};
c.document={querySelectorAll:selector=>selector==='[data-offer-id]'?[card]:[]};
c.TABO_CURATED_OFFERS=JSON.parse(fs.readFileSync('content/offers.json'));
c.TABO_OFFER_BOOK_NAMES={};
c.TaboOfferUI.render(fixture[0].config,'ar');
assert.equal(buy.hidden,true);assert.equal(elements.get('[data-offer-original]').hidden,true);
assert.match(elements.get('[data-offer-availability]').textContent,/بالدولار فقط/);
c.TaboOfferUI.render(fixture[1].config,'en');
assert.equal(buy.hidden,false);assert.equal(buy.disabled,false);
assert.match(elements.get('[data-offer-price]').textContent,/20\.00/);
assert.match(elements.get('[data-offer-original]').textContent,/31\.89/);
assert(!events.some(e=>e.name==='Purchase'));
for(const route of ['/offers',...Object.keys(JSON.parse(fs.readFileSync('content/offers.json'))).map(id=>'/offers/'+id)]){
 const source=rendered('offers.html',route);
 for(const script of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g))if(!/application\/(ld\+)?json/.test(script[1]))new vm.Script(script[2]);
 assert(source.includes('data-offer-buy'));
}
console.log(`Curated offers: ${count} Python/browser pricing comparisons, exact pack totals, Sigma, overlap, removal, upgrade, duplicate prevention, ad parameters and single AddToCart passed.`);
