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
for country in ('EG','SA'):
 c=localization.pricing_for_country(catalog.PRICES,country);cases=[]
 for mask in range(1<<len(members)):
  selected=[b for i,b in enumerate(members) if mask&(1<<i)]
  for extra in ([],['book1']):
   ids=selected+extra;cases.append({'ids':ids,'total':catalog.total_for_items(ids,c)})
 result.append({'config':c,'cases':cases})
print(json.dumps(result))
`],{encoding:'utf8',maxBuffer:8*1024*1024}));
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
  const result=vm.runInContext('calculatePricing()',c);
  assert.equal(result.final,sample.total,group.config.currency+':'+sample.ids);count++;
 }
}
// Every possible single-book addition stays nondecreasing and receives at
// least the stated quantity saving, including carts that contain curated packs.
for(const group of fixture){
 const key=ids=>[...ids].sort().join(','),totals=new Map(group.cases.map(x=>[key(x.ids),Math.round(x.total*100)]));
 const members=[...new Set(group.config.offers.flatMap(o=>o.books).concat('book1'))];
 const unit=Math.round(group.config.prices['1']*100);
 for(const sample of group.cases)for(const id of members){
  if(sample.ids.includes(id))continue;
  const next=totals.get(key([...sample.ids,id]));if(next===undefined)continue;
  const current=Math.round(sample.total*100);
  assert(next>=current,'Adding a book reduced the total');
  assert(next-current<=unit-(sample.ids.length?100:0),'Extra-book quantity saving was lost');
 }
}
for(const group of fixture){
 c.storeConfig={...group.config,offers:[...group.config.offers].reverse()};c.PRICING_LADDER=c.storeConfig.prices;
 for(const offer of group.config.offers){
  c.cart=[];events.length=0;
  c.addCuratedOffer(offer.id);c.addCuratedOffer(offer.id);
  assert.equal(c.cart.length,offer.books.length);
  assert.equal(vm.runInContext('calculatePricing().final',c),offer.price);
  assert.equal(events.length,1);assert.equal(events[0].data.value,offer.price);
  assert.deepEqual(Array.from(c.TaboOfferPricing.best(c.cart.map(x=>x.id),c.storeConfig).offerIds),[offer.id]);
  for(const removed of offer.books){
   assert(!c.TaboOfferPricing.best(offer.books.filter(id=>id!==removed),c.storeConfig).offerIds.includes(offer.id));
  }
 }
 c.cart=[{id:'book20'},{id:'book21'},{id:'book1'}];
 assert.equal(vm.runInContext('calculatePricing().final',c),group.config.currency==='EGP'?428.98:11.98);
 c.cart=[{id:'book20'},{id:'book21'},{id:'book22'},{id:'book23'},{id:'book24'}];
 const result=c.TaboOfferPricing.best(c.cart.map(x=>x.id),c.storeConfig);
 assert.equal(result.totalMinor,group.config.currency==='EGP'?68898:1798);
 const covered=result.offerIds.flatMap(id=>group.config.offers.find(o=>o.id===id).books);
 assert.equal(covered.length,new Set(covered).size);
}
c.storeConfig=fixture[1].config;c.PRICING_LADDER=c.storeConfig.prices;c.cart=[];events.length=0;
assert.equal(c.addCuratedOffer('unknown'),false);assert.equal(events.length,0);
c.addCuratedOffer('change-starter');assert.equal(events[0].data.value,10.99);
c.addCuratedOffer('self-growth');assert.equal(events[1].data.value,6);
assert.equal(vm.runInContext('calculatePricing().final',c),16.99);
c.cart=[];
c.location.search='?offer=money-and-wealth&utm_source=facebook&utm_campaign=wealth&fbclid=synthetic%2Btest&cart=1';
c.completeOfferIntent();assert.equal(c.cart.length,4);assert.equal(vm.runInContext('calculatePricing().final',c),13.99);
assert(saved.every(items=>new Set(items.map(x=>x.id)).size===items.length));
const after=new URLSearchParams(histories.at(-1).split('?')[1]);
assert.equal(after.get('utm_source'),'facebook');assert.equal(after.get('fbclid'),'synthetic+test');assert.equal(after.has('offer'),false);
vm.runInContext(fs.readFileSync('offer-ui.js','utf8'),c);
const destination=c.TaboOfferUI.cartUrl('self-growth','?utm_source=instagram&fbclid=synthetic%2Fid');
const params=new URLSearchParams(destination.split('?')[1]);assert.equal(params.get('fbclid'),'synthetic/id');assert.equal(params.get('offer'),'self-growth');
const elements=new Map();
const element=key=>{if(!elements.has(key))elements.set(key,{textContent:'',hidden:false,setAttribute(name,value){this[name]=value;}});return elements.get(key);};
const buy=element('buy');
const card={dataset:{offerId:'sigma-couple'},querySelector:element,querySelectorAll:selector=>selector==='[data-offer-buy]'||selector==='[data-offer-buy],[data-add-offer]'?[buy]:[]};
c.document={querySelectorAll:selector=>selector==='[data-offer-id]'?[card]:[]};
c.TABO_CURATED_OFFERS=JSON.parse(fs.readFileSync('content/offers.json'));c.TABO_OFFER_BOOK_NAMES={};
for(const [config,price,original] of [[fixture[0].config,/279[.,]99/,/298[.,]98/],[fixture[1].config,/7[.,]99/,/8[.,]98/]]){
 c.TaboOfferUI.render(config,'en');assert.equal(buy.hidden,false);assert.equal(buy.disabled,false);
 assert.match(elements.get('[data-offer-price]').textContent,price);
 assert.match(elements.get('[data-offer-original]').textContent,original);
}
assert(!events.some(e=>e.name==='Purchase'));
for(const route of ['/offers',...Object.keys(JSON.parse(fs.readFileSync('content/offers.json'))).map(id=>'/offers/'+id)]){
 const source=rendered('offers.html',route);
 for(const script of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g))if(!/application\/(ld\+)?json/.test(script[1]))new vm.Script(script[2]);
 assert(source.includes('data-offer-buy'));
}
console.log(`Curated offers: ${count} Python/browser pricing comparisons; packs, extra-book discounts, overlap, removal, upgrade, duplicate prevention and ad parameters passed.`);
