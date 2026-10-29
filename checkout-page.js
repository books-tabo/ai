/* Dedicated checkout: all prices and payment readiness come from the server. */
const BOOKS=JSON.parse(document.getElementById('checkout-catalog').textContent);
const BACKEND_URL='';
let currentLanguage=localStorage.getItem('taboLanguage')||((navigator.language||'ar').toLowerCase().startsWith('en')?'en':'ar');
let storeConfig={country:'EG',currency:'EGP',prices:{},countries:[],checkoutSupported:false,paypalAvailable:false};
let PRICING_LADDER={},configLoaded=false,configRequest=0;
let cart=[];
try{
    const saved=JSON.parse(localStorage.getItem('taboBooksCart')||'[]');
    if(Array.isArray(saved))cart=saved.map(item=>({type:'book',id:item?.id||BOOKS.find(book=>book.name===item?.name)?.id})).filter(item=>BOOKS.some(book=>book.id===item.id));
    cart=[...new Map(cart.map(item=>[item.id,item])).values()];
}catch(_){cart=[];}
const CHECKOUT_COPY={
 ar:{title:'سلة الكتب وإتمام الدفع',country:'الدولة',original:'السعر السابق',final:'الإجمالي',checkout:'الدفع بالبطاقات والمحافظ و\u2066Apple Pay\u2069',delete:'حذف',emptyCart:'السلة فارغة حاليًا.',browse:'تصفح الكتب',name:'الاسم الكامل',email:'البريد الإلكتروني',phone:'رقم الهاتف',loading:'جارٍ تحميل الأسعار ووسائل الدفع…',configError:'تعذر تحميل الأسعار الآن. أعد المحاولة قبل الدفع.',retry:'إعادة المحاولة',preview:'الدفع بالبطاقات والمحافظ غير متاح حاليًا.',currencyPending:'الدفع بالبطاقات والمحافظ غير متاح بهذه العملة حاليًا.',invalidCustomer:'أدخل اسمًا وبريدًا إلكترونيًا ورقم هاتف صحيحين لإكمال الدفع.',checkoutError:'تعذر الاتصال بخادم الدفع.',applePayAvailability:'Apple Pay حسب إتاحته في كاشير وعلى الأجهزة المدعومة.',delivery:'بعد تأكيد الدفع، تنتقل تلقائيًا لتحميل كتبك.'},
 en:{title:'Your cart and checkout',country:'Country',original:'Previous price',final:'Total',checkout:'Pay by card, wallet or Apple Pay',delete:'Remove',emptyCart:'Your cart is empty.',browse:'Browse books',name:'Full name',email:'Email address',phone:'Phone number',loading:'Loading prices and payment options…',configError:'Could not load prices. Retry before paying.',retry:'Retry',preview:'Card and wallet payments are temporarily unavailable.',currencyPending:'Card and wallet payment is unavailable for this currency.',invalidCustomer:'Enter a valid name, email address and phone number to continue.',checkoutError:'Could not connect to checkout.',applePayAvailability:'Apple Pay is subject to availability in Kashier and on supported devices.',delivery:'After payment confirmation, you go straight to your book downloads.'}
};
function t(key){return CHECKOUT_COPY[currentLanguage][key]||key;}
function localizedBook(book){return {...book,name:currentLanguage==='en'?book.enName:book.name};}
function saveCart(){localStorage.setItem('taboBooksCart',JSON.stringify(cart));}
function updateCart(){
    const hasItems=configLoaded&&cart.length>0;
    document.getElementById('checkoutContent').hidden=!hasItems;
    document.getElementById('emptyCart').hidden=!configLoaded||hasItems;
    if(configLoaded){renderCart();if(cart.length){const p=calculatePricing();window.TaboAnalytics?.track('InitiateCheckout',{content_ids:cart.map(x=>x.id),value:p.final,currency:storeConfig.currency});}}
}
function applyCopy(){
    document.documentElement.lang=currentLanguage;document.documentElement.dir=currentLanguage==='ar'?'rtl':'ltr';
    document.title=t('title')+' | TABO BOOKS';
    const labels={cartTitle:'title',countryLabel:'country',originalPriceLabel:'original',nameLabel:'name',emailLabel:'email',phoneLabel:'phone',emptyCartText:'emptyCart',emptyCartLink:'browse',retryConfig:'retry',deliveryNote:'delivery',applePayAvailability:'applePayAvailability'};
    Object.entries(labels).forEach(([id,key])=>{document.getElementById(id).textContent=t(key);});
    document.getElementById('countrySelect').setAttribute('aria-label',t('country'));
    document.getElementById('languageToggle').textContent=currentLanguage==='ar'?'English':'العربية';
    document.getElementById('checkoutPhoneHelp').textContent=currentLanguage==='ar'?'لتحديد صاحب الطلب ومساعدتك عند الحاجة. نقبل الأرقام العربية والإنجليزية.':'Used to identify your order and help when needed. Arabic and English digits are accepted.';
    renderEmailHelp();showCheckoutErrors(checkoutVisibleErrors,false);
    renderCountryOptions();updateCart();
}
function renderEmailHelp(){
    document.getElementById('checkoutEmailHelp').textContent=storeConfig.receiptEmailEnabled===true?(currentLanguage==='ar'?'سنرسل تأكيد الشراء ورابط الرجوع لطلبك إلى هذا البريد.':'We will send your purchase confirmation and order link to this email.'):(currentLanguage==='ar'?'راجع البريد لسهولة التواصل بخصوص طلبك.':'Check your email address so we can help with your order.');
}
async function loadStoreConfig(country=browserCountryHint()){
    const request=++configRequest;
    configLoaded=false;window.TABO_CHECKOUT_READY=false;storeConfig.paypalAvailable=false;
    updateCart();document.getElementById('countrySelect').disabled=true;
    const notice=document.getElementById('configNotice');notice.hidden=false;notice.textContent=t('loading');
    document.getElementById('retryConfig').hidden=true;
    try{
        const response=await fetch('/api/store-config?country='+encodeURIComponent(country),{cache:'no-store'});
        if(!response.ok)throw Error();const data=await response.json();
        if(request!==configRequest)return;
        if(!data.prices||!Array.isArray(data.countries)||!data.bookPolicies)throw Error();
        storeConfig=data;PRICING_LADDER=data.prices;configLoaded=true;
        localStorage.setItem('taboCountry',data.country);
        window.TABO_CHECKOUT_READY=data.checkoutEnabled===true&&data.checkoutSupported===true;
        notice.hidden=true;renderEmailHelp();renderCountryOptions();updateCart();
        const unavailable=document.getElementById('checkoutNotice');unavailable.hidden=window.TABO_CHECKOUT_READY;
        unavailable.textContent=data.checkoutSupported?t('preview'):t('currencyPending');
    }catch(_){if(request===configRequest){notice.textContent=t('configError');document.getElementById('retryConfig').hidden=false;}}
    finally{if(request===configRequest)document.getElementById('countrySelect').disabled=false;}
}
document.getElementById('cartItems').addEventListener('click',event=>{
    const button=event.target.closest('[data-cart-index]');if(!button||checkoutInFlight)return;
    const index=Number(button.dataset.cartIndex);if(!Number.isInteger(index)||index<0||index>=cart.length)return;
    cart.splice(index,1);saveCart();updateCart();
});
document.getElementById('countrySelect').addEventListener('change',event=>{if(!checkoutInFlight)loadStoreConfig(event.target.value);});
document.getElementById('languageToggle').addEventListener('click',()=>{currentLanguage=currentLanguage==='ar'?'en':'ar';localStorage.setItem('taboLanguage',currentLanguage);applyCopy();});
document.getElementById('retryConfig').addEventListener('click',()=>loadStoreConfig());
for(const key of ['name','email','phone']){
    const field=document.getElementById('checkout'+key[0].toUpperCase()+key.slice(1));
    field.addEventListener('input',()=>{const errors={...checkoutVisibleErrors};delete errors[key];showCheckoutErrors(errors,false);});
}
document.getElementById('checkoutPhone').addEventListener('blur',event=>{event.target.value=normalizeCheckoutPhone(event.target.value);});
document.getElementById('checkoutBtn').addEventListener('click',()=>checkoutWithPaymentLink());
document.getElementById('paypalCheckoutBtn').addEventListener('click',()=>checkoutWithPaymentLink('paypal'));
window.addEventListener('pageshow',event=>{
    if(!event.persisted)return;
    checkoutInFlight=false;
    ['languageToggle','checkoutName','checkoutEmail','checkoutPhone'].forEach(id=>{document.getElementById(id).disabled=false;});
    try{
        const saved=JSON.parse(localStorage.getItem('taboBooksCart')||'[]');
        cart=Array.isArray(saved)?saved.map(item=>({type:'book',id:item?.id||BOOKS.find(book=>book.name===item?.name)?.id})).filter(item=>BOOKS.some(book=>book.id===item.id)):[];
        cart=[...new Map(cart.map(item=>[item.id,item])).values()];
    }catch(_){cart=[];}
    loadStoreConfig();
});
applyCopy();loadStoreConfig();
