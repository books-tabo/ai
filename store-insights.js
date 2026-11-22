/* First-party session counts. No contact fields, URL queries, click values or Purchase events. */
(()=>{'use strict';if(window.TaboOwnerInsights)return;
const p=location.pathname;if(!/^\/$|^\/books\/book[1-9][0-9]*$|^\/(checkout|offers(?:\/[a-z0-9-]+)?|categories(?:\/[a-z0-9-]+)?|authors(?:\/[a-z0-9-]+)?|about|contact|privacy|refund|delivery)$/.test(p))return;
const q=new URLSearchParams(location.search);let ref='',country='';try{country=localStorage.getItem('taboCountry')||''}catch(_){}try{ref=new URL(document.referrer).origin}catch(_){}
const post=(path,data)=>fetch('/api/store-insights/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),cache:'no-store',keepalive:true});
const ready=post('session',{path:p,country,source:q.get('utm_source')||'',medium:q.get('utm_medium')||'',googleClick:['gclid','gbraid','wbraid'].some(k=>q.has(k)),metaClick:q.has('fbclid'),snapClick:q.has('ScCid'),referrer:ref}).then(r=>r.ok).catch(()=>false);
const seen=new Set();function track(name){if(!['ViewContent','AddToCart','InitiateCheckout'].includes(name)||seen.has(name))return;seen.add(name);ready.then(ok=>{if(ok)return post('event',{event:name})}).catch(()=>{});}
window.TaboOwnerInsights={track};const analytics=window.TaboAnalytics;
if(analytics){const original=analytics.track;analytics.track=function(name,...args){track(name);return original.call(this,name,...args)}}
else window.TaboAnalytics={track,purchase(){}};
})();
