/* Optional error-only Sentry integration. Never attach order/customer data. */
(()=>{'use strict';
 const script=document.currentScript,dsn=script?.dataset.sentryDsn,release=script?.dataset.release;
 if(!dsn||!/^tabo-[a-f0-9]{16}$/.test(release||''))return;
 const codes=new Set(['checkout_config_failed','checkout_request_failed','download_transfer_failed']);
 const errors=new Set(['Error','TypeError','ReferenceError','SyntaxError','RangeError','URIError','EvalError','AggregateError']);
 const assets=new Set(['/checkout-core.js','/checkout-page.js','/download-progress.js','/book-sharing.js','/seo.js','/index.html','/book.html','/success.html','/offers.html','/checkout','/offers','/']);
 function safePath(path){return assets.has(path)||/^\/books\/book[1-9][0-9]{0,3}$/.test(path)?path:'/';}
 const page=safePath(location.pathname),queue=[],seen=new Set();let sdk=null,sent=0;
 function clean(event){
  if(event.type&&event.type!=='error')return null;
  const code=codes.has(event.tags?.code)?event.tags.code:null;
  const values=(event.exception?.values||[]).slice(0,1).map(value=>{
   const frames=(value.stacktrace?.frames||[]).slice(-15).flatMap(frame=>{
    try{const url=new URL(frame.filename,location.origin);if(url.origin!==location.origin||safePath(url.pathname)!==url.pathname)return [];
     return [{filename:'https://tabobooks.com'+url.pathname,in_app:true,...(Number.isInteger(frame.lineno)?{lineno:frame.lineno}:{}),...(Number.isInteger(frame.colno)?{colno:frame.colno}:{})}];
    }catch(_){return [];}
   });
   return {type:errors.has(value.type)?value.type:'Error',value:'Browser error (details removed)',...(frames.length?{stacktrace:{frames}}:{})};
  });
  if(!code&&!values.length)return null;
  const result={platform:'javascript',level:'error',environment:'production',release,
   request:{url:'https://tabobooks.com'+page},tags:{page,...(code?{code}:{})},
   ...(code?{message:code}:{exception:{values}})};
  if(/^[a-f0-9]{32}$/.test(event.event_id||''))result.event_id=event.event_id;
  if(typeof event.timestamp==='number')result.timestamp=event.timestamp;
  const key=JSON.stringify([code,values,page]);if(sent>=10||seen.has(key))return null;
  sent++;seen.add(key);return result;
 }
 function capture(code){try{if(!codes.has(code))return;if(sdk)sdk.captureMessage(code,{level:'error',tags:{code}});else if(queue.length<3&&!queue.includes(code))queue.push(code);}catch(_){}}
 window.TaboErrors=Object.freeze({capture});
 const loader=document.createElement('script');loader.src='https://browser.sentry-cdn.com/11.10.0/bundle.min.js';
 loader.integrity='sha384-O/8YQaGGVy/n9jIUzcOiaiJVm4t5+JMNiK/zOa0fWvlSld+FquXemEcwJxQ3x/Lr';
 loader.crossOrigin='anonymous';loader.referrerPolicy='no-referrer';loader.async=true;loader.nonce=script.nonce;
 loader.onload=()=>{try{
  const s=window.Sentry;if(!s?.init||!s.globalHandlersIntegration)return;
  s.init({dsn,release,environment:'production',defaultIntegrations:false,
   integrations:[s.globalHandlersIntegration({onerror:true,onunhandledrejection:true})],
   dataCollection:{userInfo:false,cookies:false,httpHeaders:false,httpBodies:[],urlQueryParams:false,graphQL:{document:false,variables:false},genAI:{inputs:false,outputs:false},mcp:{inputs:false,outputs:false},databaseQueryData:false},
   sendClientReports:false,autoSessionTracking:false,maxBreadcrumbs:0,beforeBreadcrumb:()=>null,
   traceLifecycle:'static',tracesSampleRate:0,replaysSessionSampleRate:0,replaysOnErrorSampleRate:0,enableLogs:false,
   beforeSend:clean,beforeSendTransaction:()=>null,beforeSendLog:()=>null});
  sdk=s;queue.splice(0).forEach(capture);
 }catch(_){/* Monitoring cannot block shopping or delivery. */}};
 loader.onerror=()=>{queue.length=0;};document.head.appendChild(loader);
})();
