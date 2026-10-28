/* Shared cart pricing, quote validation and gateway navigation. */

const TIMEZONE_COUNTRIES = {
    "Africa/Cairo":"EG", "Asia/Riyadh":"SA", "Asia/Dubai":"AE",
    "Asia/Kuwait":"KW", "Asia/Qatar":"QA", "Asia/Bahrain":"BH",
    "Asia/Muscat":"OM", "Asia/Amman":"JO", "Asia/Jerusalem":"PS",
    "Asia/Baghdad":"IQ", "Asia/Beirut":"LB", "Asia/Damascus":"SY",
    "Asia/Aden":"YE", "Africa/Khartoum":"SD", "Africa/Mogadishu":"SO",
    "Africa/Djibouti":"DJ", "Indian/Comoro":"KM", "Africa/Tripoli":"LY",
    "Africa/Tunis":"TN", "Africa/Algiers":"DZ", "Africa/Casablanca":"MA",
    "Africa/Nouakchott":"MR"
};

function browserCountryHint(){
    const saved=localStorage.getItem("taboCountry");
    if(saved) return saved;
    const locale=(navigator.language||"").replace("_","-").split("-");
    const region=(locale[1]||"").toUpperCase();
    if(Object.values(TIMEZONE_COUNTRIES).includes(region)) return region;
    try { return TIMEZONE_COUNTRIES[Intl.DateTimeFormat().resolvedOptions().timeZone] || "EG"; }
    catch(error){ return "EG"; }
}

function formatMoney(value){
    const amount=Number(value)||0;
    try {
        if(storeConfig.currency==="EGP"){
            const digits=new Intl.NumberFormat("en-US",{
                minimumFractionDigits:Number.isInteger(amount)?0:2,
                maximumFractionDigits:2
            }).format(amount);
            return currentLanguage==="ar"?`${digits} جنيه`:`EGP ${digits}`;
        }
        return new Intl.NumberFormat("en-US",{
            style:"currency",currency:storeConfig.currency,maximumFractionDigits:2
        }).format(amount);
    } catch(error){ return `${value} ${storeConfig.currency}`; }
}

function renderCountryOptions(){
    const select=document.getElementById("countrySelect");
    if(!select||!storeConfig.countries.length) return;
    select.innerHTML=storeConfig.countries.map(country=>
        `<option value="${country.code}">${currentLanguage==="ar"?country.nameAr:country.nameEn}</option>`
    ).join("");
    select.value=storeConfig.country;
}



/* Catalog pricing start */
function bookPolicy(book){
    return {...{price:book.fixedPrices?.[storeConfig.currency]??PRICING_LADDER[1],bundleEligible:book.bundleEligible!==false,previewEnabled:book.previewEnabled!==false},...(storeConfig.bookPolicies?.[book.id]||{})};
}
function bookPrice(book){return Number(bookPolicy(book).price)}
function fixedPriceText(){return currentLanguage==="ar"?"سعر مستقل — خارج خصومات العدد، مع عرض خاص لكتابَي السيجما بالدولار":"Independent price — excluded from quantity discounts; a special USD Sigma pair offer is available"}
function fixedPriceNotice(book){return bookPolicy(book).bundleEligible?"":`<div class="fixed-price-note">${fixedPriceText()}</div>`}
function calculatePricing(){
    const books=cart.map(item=>BOOKS.find(book=>book.id===item.id)).filter(Boolean);
    const ordinary=books.filter(book=>bookPolicy(book).bundleEligible);
    const minor=value=>Math.round(Number(value||0)*100);
    const fixedMinor=books.filter(book=>!bookPolicy(book).bundleEligible).reduce((total,book)=>total+minor(bookPrice(book)),0);
    const basicMinor=ordinary.length?minor(PRICING_LADDER[ordinary.length]??ordinary.length*Number(PRICING_LADDER[1]||0)):0;
    const special=typeof TaboOfferPricing!=="undefined"?TaboOfferPricing.best(books.map(book=>book.id),{...storeConfig,prices:PRICING_LADDER,bookPolicies:Object.fromEntries(books.map(book=>[book.id,bookPolicy(book)]))}):{totalMinor:basicMinor+fixedMinor,offerIds:[],savingsMinor:0};
    const originalMinor=ordinary.length*minor(storeConfig.compareAtPrice||PRICING_LADDER[1])+fixedMinor;
    return {offerIds:special.offerIds,offerSavings:special.savingsMinor/100,count:books.length,standardCount:ordinary.length,fixedTotal:fixedMinor/100,original:originalMinor/100,final:special.totalMinor/100,discount:Math.max(0,originalMinor-special.totalMinor)/100};
}
/* Catalog pricing end */

function renderCart(){

    const container =
        document.getElementById(
            "cartItems"
        );


    if(!container){

        return;

    }


    const pricing =
        calculatePricing();

    const offerSummary=document.getElementById("cartOfferSummary");
    if(offerSummary){
        const applied=(storeConfig.offers||[]).filter(offer=>pricing.offerIds.includes(offer.id));
        offerSummary.hidden=!applied.length;
        offerSummary.textContent=applied.map(offer=>offer.title[currentLanguage]+" — "+formatMoney(offer.price)).join(" | ")+(applied.length?(currentLanguage==="ar"?" — تم تطبيق سعر الباقة تلقائيًا.":" — Pack price applied automatically."):"");
    }
    const usdSummary=needsEgpQuote();
    document.getElementById("cartFinalRow").classList.toggle("usd-summary",usdSummary);
    document.getElementById("finalPriceLabel").textContent=usdSummary?(currentLanguage==="ar"?"الإجمالي بالدولار":"Total in USD"):t("final");
    document.getElementById("checkoutBtn").textContent=usdSummary?`${t("checkout")} — ${formatMoney(pricing.final)}`:t("checkout");


    if(
        cart.length === 0
    ){
        document.getElementById("cartOriginal").parentElement.hidden=true;

        container.innerHTML = `

            <div
                style="
                text-align:center;
                padding:30px 10px;
                color:#777;
                "
            >
                ${t("emptyCart")}
            </div>

        `;


        document.getElementById(
            "cartOriginal"
        ).textContent =
            formatMoney(0);


        document.getElementById(
            "cartFinal"
        ).textContent =
            formatMoney(0);


        document.getElementById(
            "cartNextMessage"
        ).textContent =
            "";


        document.getElementById(
            "checkoutBtn"
        ).disabled =
            true;

        renderPayPalChoice();
        refreshCheckoutQuote();

        return;

    }


    document.getElementById("checkoutBtn").disabled = !window.TABO_CHECKOUT_READY||checkoutInFlight;


    container.innerHTML =

        cart.map(
            (item,index)=>{ const book=localizedBook(BOOKS.find(book=>book.id===item.id)); return `

                <div class="cart-item">

                    <div>

                        <div
                            class="cart-item-name"
                        >
                            ${book.name}
                        </div>

                        <div
                            class="cart-item-price"
                        >
                            ${(pricing.offerIds.some(id=>(storeConfig.offers||[]).find(o=>o.id===id)?.books.includes(item.id))?(currentLanguage==="ar"?"ضمن سعر الباقة":"Included in pack"):formatMoney(bookPrice(book)))}
                        </div>
                        ${fixedPriceNotice(book)}

                    </div>

                    <button
                        class="delete-item"
                        data-cart-index="${index}"
                        title="${t("delete")}"
                    >
                        ×
                    </button>

                </div>

            `;}
        ).join("");


    document.getElementById(
        "cartOriginal"
    ).textContent =

        formatMoney(pricing.original);


    document.getElementById(
        "cartFinal"
    ).textContent =

        formatMoney(pricing.final);


    const nextCount=pricing.standardCount+1;
    let nextMessage="";
    if(PRICING_LADDER[nextCount] && !(storeConfig.offers||[]).length){
        const nextPrice=Math.round((Number(PRICING_LADDER[nextCount])+pricing.fixedTotal)*100)/100;
        const difference=Math.round((nextPrice-pricing.final)*100)/100;
        nextMessage=t("addAnother").replace("{difference}",formatMoney(difference)).replace("{next}",formatMoney(nextPrice));
    }
    if((storeConfig.offers||[]).length)nextMessage=currentLanguage==="ar"?"يُحسب أقل إجمالي مستحق تلقائيًا. حذف كتاب قد يغيّر الباقة المتاحة.":"The lowest eligible total is calculated automatically. Removing a book may change pack eligibility.";
    if(pricing.count>pricing.standardCount) nextMessage=[nextMessage,fixedPriceText()].filter(Boolean).join(" ");
    document.getElementById("cartNextMessage").textContent=nextMessage;
    document.getElementById("cartOriginal").parentElement.hidden=pricing.discount===0;

    renderPayPalChoice();
    refreshCheckoutQuote();
}




/* Customer fields start */
let checkoutVisibleErrors={};
function normalizeCheckoutPhone(value){
    const digits='٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹';
    let phone=String(value||'').replace(/[٠-٩۰-۹]/g,c=>String(digits.indexOf(c)%10)).replace(/[\s().\-\u200e\u200f\u061c]/g,'');
    return phone.startsWith('00')?'+'+phone.slice(2):phone;
}
function checkoutFieldErrors(customer){
    const errors={};
    if(customer.name.length<2||customer.name.length>100||/[\x00-\x1f]/.test(customer.name))errors.name='invalid';
    if(customer.email.length>254||!/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(customer.email)||/[\x00-\x1f]/.test(customer.email))errors.email='invalid';
    if(!/^\+?[0-9]{8,15}$/.test(customer.phone))errors.phone='invalid';
    return errors;
}
function showCheckoutErrors(errors,focus=true){
    checkoutVisibleErrors=errors;
    const copy=currentLanguage==='en'?{name:'Enter your full name (2–100 characters).',email:'Enter a valid email, such as name@example.com.',phone:'Enter a valid phone number (8–15 digits), with the country code if needed.'}:{name:'أدخل اسمك الكامل من حرفين إلى 100 حرف.',email:'أدخل بريدًا صحيحًا، مثل name@example.com.',phone:'أدخل رقم هاتف صحيحًا من 8 إلى 15 رقمًا، مع مفتاح الدولة عند الحاجة.'};
    let first=null,inline=false;
    for(const key of ['name','email','phone']){
        const field=document.getElementById('checkout'+key[0].toUpperCase()+key.slice(1)),note=document.getElementById('checkout'+key[0].toUpperCase()+key.slice(1)+'Error');
        if(note){inline=true;note.hidden=!errors[key];note.textContent=errors[key]?copy[key]:'';field.setAttribute('aria-invalid',errors[key]?'true':'false');}
        if(errors[key]&&!first)first=field;
    }
    if(first&&focus){if(inline)first.focus?.();else alert(t('invalidCustomer'));}
}
function checkoutStatus(message){
    const node=document.getElementById('checkoutStatus');
    if(node){node.textContent=String(message||'').slice(0,220);node.hidden=!message;}
    else if(message)alert(message);
}
/* Customer fields end */

/* EGP quote start */
let checkoutQuote=null, checkoutQuoteKey="", checkoutQuotePending="", checkoutQuoteRequest=0, checkoutInFlight=false;

function needsEgpQuote(){
    return storeConfig.paymentProvider==='kashier' && storeConfig.currency==='USD' && cart.length>0;
}
function quoteCartKey(){
    return JSON.stringify([storeConfig.country,storeConfig.currency,calculatePricing().final,cart.map(x=>x.id).sort()]);
}
function currentCheckoutQuote(){
    return checkoutQuote && checkoutQuoteKey===quoteCartKey() && checkoutQuote.expiresAt*1000>Date.now()+5000;
}
function displayChargeQuote(){
    const note=document.getElementById('chargeCurrencyNote');
    const amount=new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(checkoutQuote.chargeTotal));
    note.textContent=currentLanguage==='en'?`Cards/wallets: Kashier will charge EGP ${amount}. Your bank determines any currency conversion and fees.`:`البطاقات والمحافظ: سيتم تحصيل ${amount} جنيه مصري عبر كاشير؛ يحدد بنكك تحويل العملة وأي رسوم.`;
    note.style.display='block';
}
async function refreshCheckoutQuote(force=false){
    const note=document.getElementById('chargeCurrencyNote'),button=document.getElementById('checkoutBtn');
    if(!needsEgpQuote() || !window.TABO_CHECKOUT_READY){
        checkoutQuote=null;checkoutQuotePending='';checkoutQuoteRequest++;
        note.style.display='none';return false;
    }
    const key=quoteCartKey();
    if(!force && currentCheckoutQuote()){displayChargeQuote();return true;}
    button.disabled=true;
    if(checkoutQuotePending===key)return false;
    checkoutQuote=null;checkoutQuotePending=key;
    const request=++checkoutQuoteRequest;
    note.style.display='block';
    note.textContent=currentLanguage==='en'?'Calculating the amount in Egyptian pounds…':'جارٍ حساب المقابل بالجنيه المصري…';
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
    try{
        const response=await fetch(`${BACKEND_URL}/api/checkout-quote`,{
            method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
            body:JSON.stringify({items:cart.map(x=>({id:x.id})),country:storeConfig.country})
        });
        const data=await response.json(),quote=data.quote;
        if(!response.ok || !data.ok || !quote || quote.chargeCurrency!=='EGP' ||
                quote.displayCurrency!=='USD' || !Number.isFinite(Number(quote.chargeTotal)) ||
                Number(quote.chargeTotal)<=0 || Number(quote.displayTotal)!==calculatePricing().final ||
                !Number.isFinite(quote.expiresAt) || quote.expiresAt*1000<=Date.now()+5000 || !quote.token){throw new Error('quote');}
        if(request!==checkoutQuoteRequest || key!==quoteCartKey())return false;
        checkoutQuote=quote;checkoutQuoteKey=key;displayChargeQuote();return true;
    }catch(error){
        if(request===checkoutQuoteRequest){
            note.textContent=currentLanguage==='en'?'Could not calculate the EGP amount. Press Continue to payment to retry.':'تعذر حساب المقابل بالجنيه. اضغط متابعة الدفع للمحاولة مجددًا.';
        }
        return false;
    }finally{
        clearTimeout(timeout);
        if(request===checkoutQuoteRequest){checkoutQuotePending='';button.disabled=!window.TABO_CHECKOUT_READY||checkoutInFlight;}
    }
}
/* EGP quote end */

function renderPayPalChoice(){
    const button=document.getElementById('paypalCheckoutBtn'),note=document.getElementById('paypalCheckoutNote');
    if(!button||!note)return;
    const available=storeConfig.paypalAvailable===true&&storeConfig.currency==='USD'&&cart.length>0;
    button.hidden=note.hidden=!available;button.disabled=checkoutInFlight||!available;
    button.textContent=currentLanguage==='en'?'Pay with PayPal in USD':'الدفع عبر PayPal بالدولار';
    note.textContent=currentLanguage==='en'?'PayPal: the displayed total is charged in US dollars.':'PayPal: سيتم تحصيل الإجمالي المعروض بالدولار الأمريكي.';
}

async function checkoutWithPaymentLink(){
    const selected=arguments[0]==='paypal'?'paypal':storeConfig.paymentProvider;
    if(checkoutInFlight)return;
    const useQuote=selected!=='paypal'&&needsEgpQuote();
    if(selected==='paypal'?storeConfig.paypalAvailable!==true||storeConfig.currency!=='USD':!window.TABO_CHECKOUT_READY){
        alert(storeConfig.checkoutSupported?t('preview'):t('currencyPending'));
        return;
    }
    const pricing=calculatePricing();
    if(!pricing.count) return;
    checkoutStatus('');
    const customer={
        name:document.getElementById('checkoutName').value.trim(),
        email:document.getElementById('checkoutEmail').value.trim(),
        phone:normalizeCheckoutPhone(document.getElementById('checkoutPhone').value)
    };
    const fieldErrors=checkoutFieldErrors(customer);showCheckoutErrors(fieldErrors);
    if(Object.keys(fieldErrors).length)return;
    document.getElementById('checkoutPhone').value=customer.phone;
    if(useQuote && !currentCheckoutQuote()){
        if(await refreshCheckoutQuote())checkoutStatus(currentLanguage==='en'?'The EGP amount has been updated. Please review it and press Continue to payment.':'تم تحديث المقابل بالجنيه. راجعه ثم اضغط متابعة الدفع.');
        return;
    }
    const button=document.getElementById('checkoutBtn'),paypalButton=document.getElementById('paypalCheckoutBtn');
    checkoutInFlight=true;button.disabled=true;if(paypalButton)paypalButton.disabled=true;
    ['countrySelect','languageToggle','checkoutName','checkoutEmail','checkoutPhone'].forEach(id=>{const field=document.getElementById(id);if(field)field.disabled=true;});
    try {
        const response=await fetch(`${BACKEND_URL}/api/orders`,{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({items:cart.map(item=>({id:item.id})),customer,
                country:storeConfig.country,language:currentLanguage,paymentProvider:selected,
                quoteToken:useQuote?checkoutQuote.token:undefined})
        });
        const data=await response.json();
        if(!response.ok||!data.ok){
            if(data.code==='QUOTE_REFRESH_REQUIRED')await refreshCheckoutQuote(true);
            const error=new Error(data.error||t('checkoutError'));
            if(data.code==='INVALID_CUSTOMER'&&data.fields)error.fieldErrors=data.fields;
            throw error;
        }
        window.TaboTracking?.track('InitiateCheckout',{content_ids:data.order.items.map(item=>item.id),value:Number(data.order.total),currency:data.order.currency});
        const orderId=data.order.id;
        localStorage.setItem('taboLastOrderId',orderId);
        const key=new URL(data.tracking_url,location.origin).searchParams.get('key');
        sessionStorage.setItem('taboPayment:'+key,data.payment_url);
        window.location.assign(data.payment_url);
    } catch(error){checkoutInFlight=false;button.disabled=!window.TABO_CHECKOUT_READY;if(paypalButton)paypalButton.disabled=storeConfig.paypalAvailable!==true;['countrySelect','languageToggle','checkoutName','checkoutEmail','checkoutPhone'].forEach(id=>{const field=document.getElementById(id);if(field)field.disabled=false;});if(error.fieldErrors)showCheckoutErrors(error.fieldErrors);else checkoutStatus(error.message||t('checkoutError'));}
}

window.TABO_CHECKOUT_READY=false;
