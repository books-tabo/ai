/* Exercises the actual storefront quote/payment scripts, with synthetic HTTP only. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){
    if(!/application\/ld\+json/.test(match[1]))new vm.Script(match[2]);
}
const quoteSource=html.split('/* EGP quote start */')[1].split('/* EGP quote end */')[0];
const checkoutSource=html.split('async function checkoutWithPaymentLink(){')[1].split('window.TABO_CHECKOUT_READY=false;')[0];
assert.match(html, /id="chargeCurrencyNote"[^>]*font-size:13px/);
function boot(){
    const nodes={
        chargeCurrencyNote:{style:{},textContent:''},checkoutBtn:{disabled:false},
        checkoutName:{value:'Synthetic Buyer'},checkoutEmail:{value:'synthetic@example.invalid'},
        checkoutPhone:{value:'01000000000'}
    };
    const calls=[],alerts=[],events=[],redirects=[];
    const c=vm.createContext({
        document:{getElementById:id=>nodes[id]},
        storeConfig:{paymentProvider:'kashier',currency:'USD',country:'KW',checkoutSupported:true},
        cart:[{id:'book1'}],currentLanguage:'ar',BACKEND_URL:'https://synthetic.invalid',
        window:{TABO_CHECKOUT_READY:true,TaboTracking:{track:(...a)=>events.push(a)},
                location:{assign:u=>redirects.push(u)}},
        location:{origin:'https://synthetic.invalid'},
        calculatePricing:()=>({final:9.99,count:c.cart.length}),
        Date,Intl,AbortController,URL,JSON,Number,
        setTimeout:()=>1,clearTimeout:()=>{},
        localStorage:{setItem:()=>{}},sessionStorage:{setItem:()=>{}},
        alert:m=>alerts.push(m),t:k=>k
    });
    const quote=()=>({chargeCurrency:'EGP',displayCurrency:'USD',displayTotal:'9.99',
                     chargeTotal:'541.16',expiresAt:Math.floor(Date.now()/1000)+300,token:'synthetic-signed'});
    c.fetch=async(url,options)=>{
        calls.push({url,options});
        return {ok:true,json:async()=>({ok:true,quote:quote()})};
    };
    vm.runInContext(quoteSource+'\nasync function checkoutWithPaymentLink(){'+checkoutSource,c);
    return {c,nodes,calls,alerts,events,redirects,quote};
}
(async()=>{
    {
        const {c,nodes,calls}=boot();
        assert.equal(await c.refreshCheckoutQuote(),true);
        assert.match(nodes.chargeCurrencyNote.textContent,/541.16 جنيه/);
        assert.equal(nodes.chargeCurrencyNote.style.display,'block');
        assert.equal(nodes.checkoutBtn.disabled,false);
        await c.refreshCheckoutQuote();
        assert.equal(calls.length,1);
        c.currentLanguage='en';await c.refreshCheckoutQuote();
        assert.match(nodes.chargeCurrencyNote.textContent,/EGP 541.16/);
        c.storeConfig.currency='EGP';await c.refreshCheckoutQuote();
        assert.equal(nodes.chargeCurrencyNote.style.display,'none');
        assert.equal(calls.length,1);
    }
    {
        const {c,nodes}=boot();
        c.fetch=async()=>{throw Error('synthetic outage');};
        assert.equal(await c.refreshCheckoutQuote(),false);
        assert.equal(Boolean(c.currentCheckoutQuote()),false);
        assert.equal(nodes.checkoutBtn.disabled,false);
        assert.match(nodes.chargeCurrencyNote.textContent,/تعذر/);
    }
    {
        const {c,nodes,quote}=boot();let resolve;
        c.fetch=()=>new Promise(r=>{resolve=r;});
        const pending=c.refreshCheckoutQuote();
        c.cart=[{id:'book2'}];
        resolve({ok:true,json:async()=>({ok:true,quote:quote()})});
        assert.equal(await pending,false);
        assert.equal(Boolean(c.currentCheckoutQuote()),false);
        c.fetch=async()=>({ok:true,json:async()=>({ok:true,quote:quote()})});
        assert.equal(await c.refreshCheckoutQuote(),true);
        assert.match(nodes.chargeCurrencyNote.textContent,/541.16/);
    }
    {
        const {c,calls,alerts,events,redirects}=boot();
        // Missing/expired quote must be displayed before a separate confirmation.
        await c.checkoutWithPaymentLink();
        assert.equal(calls.length,1);
        assert.ok(calls[0].url.endsWith('/api/checkout-quote'));
        assert.equal(alerts.length,1);
        c.fetch=async(url,options)=>{
            calls.push({url,options});
            return {ok:true,json:async()=>({ok:true,order:{id:'TABO-0000000000000001',
                total:541.16,currency:'EGP',items:[{id:'book1'}]},
                payment_url:'https://payments.kashier.io/session/synthetic',
                tracking_url:'/pending.html?key=synthetic'})};
        };
        await c.checkoutWithPaymentLink();
        const body=JSON.parse(calls[1].options.body);
        assert.equal(body.quoteToken,'synthetic-signed');
        assert.equal(events[0][0],'InitiateCheckout');
        assert.equal(events[0][1].currency,'EGP');
        assert.equal(events[0][1].value,541.16);
        assert.equal(redirects.length,1);
    }
    console.log('4 checkout quote groups passed; all inline storefront JavaScript parsed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
