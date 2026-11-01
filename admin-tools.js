/* Loaded only on the owner dashboard. Endpoint enforces the owner session. */
(()=>{'use strict';const $=id=>document.getElementById(id);let busy=false;
 function link(text,url){const a=document.createElement('a');a.textContent=text;a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.className='secondary';return a;}
 async function open(view){if(view!=='tools'||busy)return;busy=true;$('toolsMessage').textContent='جارٍ قراءة حالة الأدوات…';
  try{const response=await fetch('/api/admin-dashboard/tools',{credentials:'same-origin',cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();
   $('speedLinks').replaceChildren();for(const page of data.pagespeed){const row=document.createElement('div');row.className='editor-actions';const label=document.createElement('strong');label.textContent=page.label;row.append(label,link('فحص الموبايل ↗',page.mobile),link('فحص الكمبيوتر ↗',page.desktop));$('speedLinks').append(row);}
   $('sentryState').textContent=data.sentry.enabled?'الربط مفعّل لإرسال أخطاء المتصفح':'الربط جاهز في المتجر؛ يتبقى إعداد حساب Sentry وتفعيله';
   $('sentryLink').replaceChildren(link('فتح Sentry ↗',data.sentry.url));$('toolsMessage').textContent='';
  }catch(_){$('toolsMessage').textContent='تعذر قراءة الحالة. تأكد من تسجيل الدخول وأعد المحاولة.';}
  finally{busy=false;}
 }
 window.TaboStoreTools={open};$('toolsReload').addEventListener('click',()=>open('tools'));
})();
