/* Owner-only UI: every value comes from an authenticated backend report. */
(()=>{'use strict';const $=id=>document.getElementById(id);let challenge='',data=null,offset=0,busy=false,view='overview';
const states={PAID:'مدفوع',PENDING:'بانتظار التأكيد',REFUNDED:'مسترد',VOIDED:'ملغى',REVERSED:'عملية معكوسة',REVIEW:'يحتاج مراجعة'};
const names={comments:['التعليقات والمشتركون','تعليقات القرّاء.','أدر التعليقات وراجع عناوين البريد المشتركة.'],tools:['السرعة والأخطاء','متجر أسرع وأخطاء أوضح.','افحص الصفحات العامة وتابع حالة رصد الأخطاء.'],catalog:['الكتب والعروض','أدر كتبك من مكان واحد.','حفظ مسودة ومعاينة ونشر بيانات الكتب والأغلفة والأسعار والعروض.'],recovery:['استكمال الطلبات','ساعد القارئ على إكمال اختياره.','تابع التذكيرات التي وافق عليها العملاء للطلبات غير المكتملة.'],overview:['نظرة عامة','أداء متجرك، بوضوح.','تابع مبيعات كتبك وطلباتك من بيانات المتجر الفعلية.'],orders:['الطلبات','كل طلب، بتفاصيله.','ابحث برقم الطلب وتابع الدفع والرسالة واستخدام التحميل.'],audience:['الزيارات ورحلة الشراء','افهم الطريق إلى الشراء.','جلسات ومصادر فعلية من قياس المتجر الداخلي، من وقت تفعيله.'],followup:['التسليم والمتابعة','تابع ما بعد الشراء.','رؤية لحالة رسائل الشراء والطلبات التي لم يبدأ تحميلها بعد.']};
names.promoters = ['روابط المروّجين', 'روابط المروّجين', 'أنشئ رابطًا لكل مروّج وتابع الطلبات التي جاءت من خلاله.'];
const sources={direct:'زيارة مباشرة',google:'Google',meta:'Facebook / Instagram',snapchat:'Snapchat',bing:'Bing',other:'إحالات أخرى'},mediums={direct:'مباشرة',paid:'إعلان / نقرة إعلانية',organic:'بحث طبيعي',referral:'إحالة',unknown:'غير متاح'};
const num=n=>new Intl.NumberFormat('ar-EG',{maximumFractionDigits:0}).format(Number(n)||0);
const money=(v,c)=>new Intl.NumberFormat('en-US',{style:'currency',currency:c,maximumFractionDigits:2}).format(Number(v)||0);
const when=s=>s?new Intl.DateTimeFormat('ar-EG',{timeZone:'Asia/Kuwait',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(s)):'—';
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function empty(target,text='لا توجد بيانات خلال الفترة المختارة.'){target.replaceChildren(el('div',text,'empty'));}
function message(id,text,error=true){$(id).textContent=text;$(id).className=text?(error?'error':'notice'):'';}
async function api(path,body){const r=await fetch('/api/admin-dashboard/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});let d={};try{d=await r.json()}catch(_){}if(!r.ok){const e=Error(d.error||'تعذر الاتصال. حاول مرة أخرى.');e.status=r.status;throw e}return d;}
function authenticated(on){$('loginView').hidden=on;$('dashboardView').hidden=!on;}
function selectView(v) {
  view = Object.hasOwn(names, v) ? v : 'overview';
  const [caption, heading, description] = names[view];
  $('sectionCaption').textContent = caption;
  $('viewHeading').textContent = heading;
  $('viewDescription').textContent = description;
  document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('[data-panel]').forEach(p => p.hidden = p.dataset.panel !== view);
  $('filters').hidden = $('reportCaption').hidden = ['tools', 'comments', 'promoters'].includes(view);
  if (view === 'comments') loadReaderAdmin();
  if (view === 'promoters') loadPromoters();
  if (globalThis.TaboStoreTools) globalThis.TaboStoreTools.open(view);
  if (globalThis.TaboStoreAdmin) globalThis.TaboStoreAdmin.open(view);
}

let promoterOffset = 0;
let promoterMore = false;
let promoterBusy = false;

function syncPromoterControls() {
  $('promoterReload').disabled = promoterBusy;
  $('promoterCreate').disabled = promoterBusy || !$('promoterDestination').value;
  $('promoterPrevious').disabled = promoterBusy || promoterOffset === 0;
  $('promoterNext').disabled = promoterBusy || !promoterMore;
  document.querySelectorAll('[data-promoter-toggle]').forEach(button => button.disabled = promoterBusy);
}

async function copyPromoterUrl(input) {
  try {
    await navigator.clipboard.writeText(input.value);
    message('promoterMessage', 'تم نسخ الرابط.', false);
  } catch (_) {
    input.focus();
    input.select();
    message('promoterMessage', 'تم تحديد الرابط. انسخه من الحقل.', false);
  }
}

function renderPromoters(result) {
  const select = $('promoterDestination');
  const selected = select.value;
  select.replaceChildren();
  for (const [path, label] of Object.entries(result.destinations)) {
    const option = el('option', label);
    option.value = path;
    select.append(option);
  }
  select.value = Object.hasOwn(result.destinations, selected) ? selected : '/';

  const list = $('promoterRows');
  list.replaceChildren();
  if (!result.rows.length) empty(list, 'لا توجد روابط في هذه الصفحة.');
  for (const row of result.rows) {
    const card = el('article', undefined, 'ranking-row');
    const header = el('div', undefined, 'promoter-summary');
    const state = el('span', row.active ? 'فعّال' : 'متوقف', 'status-tag ' + (row.active ? 'paid' : 'pending'));
    header.append(el('strong', row.name), state);
    const destination = el('p', result.destinations[row.destination] || row.destination, 'small-note');
    const count = el('p', 'مرات الفتح ' + num(row.opens) + ' · الطلبات المدفوعة ' + num(row.paidOrders));
    card.append(header, destination, count);
    if (row.sales.length) {
      const amounts = el('p', undefined, 'promoter-summary');
      for (const sale of row.sales) {
        const amount = el('bdi', new Intl.NumberFormat('en-US', {
          style: 'currency', currency: sale.currency, currencyDisplay: 'code',
        }).format(sale.amount), 'money');
        amounts.append(amount);
      }
      card.append(amounts);
    }
    const url = el('input');
    url.type = 'url';
    url.className = 'promoter-url';
    url.value = row.url;
    url.readOnly = true;
    url.dir = 'ltr';
    url.setAttribute('aria-label', 'رابط المروّج ' + row.name);
    const actions = el('div', undefined, 'editor-actions');
    const copy = el('button', 'نسخ الرابط', 'secondary');
    copy.type = 'button';
    copy.addEventListener('click', () => copyPromoterUrl(url));
    const toggle = el('button', row.active ? 'إيقاف' : 'تفعيل', 'secondary');
    toggle.type = 'button';
    toggle.dataset.promoterToggle = '';
    toggle.addEventListener('click', () => loadPromoters(promoterOffset, {
      action: 'setActive', code: row.code, active: !row.active,
    }));
    actions.append(copy, toggle);
    card.append(url, actions);
    list.append(card);
  }
}

async function loadPromoters(start = promoterOffset, change) {
  if (promoterBusy) return;
  promoterBusy = true;
  syncPromoterControls();
  message('promoterMessage', '');
  let notice = '';
  try {
    if (change) {
      const saved = await api('promoters', change);
      if (change.action === 'create') {
        $('promoterCreatedUrl').value = saved.url;
        $('promoterCreated').hidden = false;
        $('promoterName').value = '';
        notice = 'الرابط جاهز للنسخ والمشاركة.';
      } else {
        notice = change.active ? 'تم تفعيل الرابط.' : 'تم إيقاف تسجيل مرات الفتح الجديدة.';
      }
    }
    const result = await api('promoters?offset=' + start);
    renderPromoters(result);
    promoterOffset = start;
    promoterMore = result.more;
    $('promoterPagination').hidden = !promoterOffset && !promoterMore;
    $('promoterPage').textContent = 'صفحة ' + num(promoterOffset / 20 + 1);
    if (notice) message('promoterMessage', notice, false);
  } catch (e) {
    if (e.status === 403) {
      authenticated(false);
      message('loginMessage', 'انتهت جلسة الدخول. اطلب رمزًا جديدًا.');
    } else {
      message('promoterMessage', notice ? notice + ' تعذر تحديث قائمة الروابط.' : e.message);
    }
  } finally {
    promoterBusy = false;
    syncPromoterControls();
  }
}

$('promoterReload').addEventListener('click', () => loadPromoters());
$('promoterPrevious').addEventListener('click', () => loadPromoters(Math.max(0, promoterOffset - 20)));
$('promoterNext').addEventListener('click', () => loadPromoters(promoterOffset + 20));
$('promoterCopyCreated').addEventListener('click', () => copyPromoterUrl($('promoterCreatedUrl')));
$('promoterForm').addEventListener('submit', event => {
  event.preventDefault();
  loadPromoters(0, {action: 'create', name: $('promoterName').value, destination: $('promoterDestination').value});
});
const readerAdmin={
 comments:{target:'readerAdminComments',more:'readerCommentsMore',offset:0,loading:false},
 subscribers:{target:'readerAdminSubscribers',more:'readerSubscribersMore',offset:0,loading:false}
};
async function loadReaderAdminPage(kind, reset=false){
 const state=readerAdmin[kind],target=$(state.target),more=$(state.more);
 if(state.loading)return;
 state.loading=true;more.disabled=true;
 try{
  const start=reset?0:state.offset;
  const result=await api('comments?kind='+kind+'&offset='+start);
  if(reset)target.replaceChildren();
  for(const row of result.rows){
   const item=el('article',undefined,'ranking-row');
   if(kind==='subscribers'){
    const email=el('bdi',row.email);email.dir='ltr';
    item.append(email,el('small',' · '+when(row.consented_at)));
   }else{
    item.append(el('strong',row.display_name+' · '+row.book_name),el('small',' · '+when(row.created_at)+(row.status==='pending'?' · تعليق سابق قيد المراجعة':'')),el('p',row.body));
    const remove=el('button','حذف التعليق','secondary');remove.type='button';
    remove.addEventListener('click',async()=>{
     if(state.loading||!window.confirm('إزالة هذا التعليق من المتجر؟'))return;
     remove.disabled=true;
     try{
      await api('comments',{id:row.id});
      message('readerAdminMessage','تم حذف التعليق.',false);
      await loadReaderAdminPage('comments',true);
     }catch(e){message('readerAdminMessage',e.message)}finally{remove.disabled=false}
    });
    item.append(remove);
   }
   target.append(item);
  }
  state.offset=start+result.rows.length;
  more.hidden=!result.more;
  if(!state.offset)empty(target,kind==='comments'?'لا توجد تعليقات.':'لا يوجد مشتركون حتى الآن.');
 }catch(e){message('readerAdminMessage',e.message)}finally{state.loading=false;more.disabled=false}
}
function loadReaderAdmin(){
 message('readerAdminMessage','');
 loadReaderAdminPage('comments',true);
 loadReaderAdminPage('subscribers',true);
}
for(const [kind,state] of Object.entries(readerAdmin))$(state.more)?.addEventListener('click',()=>loadReaderAdminPage(kind));
function setRange(days){const end=new Date(Date.now()+3*3600000),start=new Date(end.getTime()-(days-1)*86400000);$('dateFrom').value=start.toISOString().slice(0,10);$('dateTo').value=end.toISOString().slice(0,10);document.querySelectorAll('[data-days]').forEach(b=>b.classList.toggle('selected',Number(b.dataset.days)===days));}
function rank(target,rows,label,value){target.replaceChildren();if(!rows.length)return empty(target);const max=Math.max(1,...rows.map(value));rows.slice(0,6).forEach((r,i)=>{const row=el('div',undefined,'ranking-row'),top=el('div',undefined,'rank-top'),title=el('div',undefined,'rank-title');title.append(el('span',String(i+1).padStart(2,'0'),'rank-number'),el('span',label(r)));top.append(title,el('span',num(value(r)),'rank-value'));const bar=el('div',undefined,'bar-track'),fill=el('div',undefined,'bar-fill');fill.style.width=String(value(r)/max*100)+'%';bar.append(fill);row.append(top,bar);target.append(row)});}
function chart(){const target=$('salesChart');if(!data)return;const currency=$('chartCurrency').value;const from=new Date(data.range.from),to=new Date(data.range.to),days=Math.round((to-from)/86400000),points=[];for(let i=0;i<days;i++){const date=new Date(from.getTime()+i*86400000+3*3600000).toISOString().slice(0,10),row=data.daily.find(x=>x.day===date&&x.currency===currency);points.push({day:date,value:row?Number(row.amount):0})}target.replaceChildren();$('chartStart').textContent=points[0]?.day||'';$('chartEnd').textContent=points.at(-1)?.day||'';if(!points.some(x=>x.value>0))return empty(target,'لا توجد مبيعات مؤكدة بهذه العملة خلال الفترة.');
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 620 210');svg.setAttribute('preserveAspectRatio','none');const max=Math.max(...points.map(p=>p.value))*1.15;
 function node(tag,attrs){const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n}
 for(let i=0;i<5;i++){const y=15+i*43;svg.append(node('line',{x1:45,x2:615,y1:y,y2:y,stroke:'#e9ede3','stroke-dasharray':'3 5'}));const t=node('text',{x:0,y:y+3,fill:'#9da78f','font-size':9});t.textContent=new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(max*(1-i/4));svg.append(t)}
 const coords=points.map((p,i)=>[45+i*570/Math.max(1,points.length-1),187-p.value/max*172]);const line=coords.map((p,i)=>(i?'L':'M')+p.join(',')).join(' ');svg.append(node('path',{d:line+' L'+coords.at(-1)[0]+',187 L45,187 Z',fill:'#dfe8d0',opacity:.6}));svg.append(node('path',{d:line,fill:'none',stroke:'#76985d','stroke-width':2.5,'vector-effect':'non-scaling-stroke'}));points.forEach((p,i)=>{const c=node('circle',{cx:coords[i][0],cy:coords[i][1],r:3,fill:'#76985d'});const title=node('title',{});title.textContent=p.day+' · '+money(p.value,currency);c.append(title);svg.append(c)});target.append(svg);}
function renderOrders(){const body=$('ordersBody');body.replaceChildren();$('orderTotal').textContent=num(data.orderCount)+' طلب ضمن فلاتر سجل الطلبات';for(const o of data.orders){const tr=el('tr'),first=el('td');first.append(el('span',o.id,'order-id'),el('small',(o.items||[]).map(x=>data.catalog[x.id]||x.name||'كتاب').join('، ')));const date=el('td',when(o.created_at));const status=el('td');status.append(el('span',states[o.status]||o.status,'status-tag '+(o.status==='PAID'?'paid':o.status==='PENDING'?'pending':'problem')));const amount=el('td');amount.append(el('span',money(o.total,o.currency),'money'));const country=el('td',data.countries[o.country]||o.country);country.append(el('small',o.payment_method));const delivery=el('td',num(o.downloads)+' / '+num(o.book_count*2)+' تحميل');const email={sent:'الرسالة مقبولة لدى المزود',pending:'الرسالة قيد الإرسال',sending:'يجري إرسال الرسالة',retry:'إعادة محاولة إرسال الرسالة',failed:'تعذر إرسال الرسالة',skipped:'لم تُرسل الرسالة'};if(o.book_emails?.length){for(const item of o.book_emails){delivery.append(el('small',(data.catalog[item.book_id]||item.book_id)+' · PDF بالبريد: '+(email[item.state]||'غير متاح')+(item.reason==='payload_changed'?' · تغيّر الملف؛ يحتاج مراجعة':'')));}}else delivery.append(el('small',email[o.email_state]||'لا توجد رسالة مسجلة'));tr.append(first,date,status,amount,country,delivery);body.append(tr)}if(!data.orders.length){const cell=el('td','لا توجد طلبات تطابق البحث.','empty');cell.colSpan=6;const row=el('tr');row.append(cell);body.append(row)}$('pageLabel').textContent='صفحة '+num(offset/20+1);$('previousPage').disabled=offset===0;$('nextPage').disabled=offset+20>=data.orderCount;}
function render(){const d=data;for(const [id,value]of Object.entries({paidOrders:d.paidOrders,pendingOrders:d.pendingOrders,sessionCount:d.sessions,pageViewCount:d.pageViews,activeCount:d.activeSessions,attributedCount:d.attributedPaidOrders,noDownloadCount:d.followup.withoutDownloads,emailRetryCount:d.followup.emailRetry,emailFailedCount:d.followup.emailFailed}))$(id).textContent=num(value);
 for(const c of ['EGP','USD'])$('revenue'+c).textContent=money(d.revenue.find(x=>x.currency===c)?.amount||0,c);$('ownerEmail').textContent=d.adminEmail;$('updatedLabel').textContent='آخر تحديث: '+when(d.updatedAt);const select=$('countryFilter');if(select.options.length===1){for(const [code,name]of Object.entries(d.countries)){const op=el('option',name);op.value=code;select.append(op)}}
 rank($('topBooks'),d.books,x=>d.catalog[x.book_id]||x.book_id,x=>Number(x.units));rank($('countryRanking'),d.countries instanceof Array?[]:d.countrySales||[],x=>d.countries[x.country]||x.country,x=>Number(x.orders));
 const pay=$('paymentRanking');pay.replaceChildren();for(const p of d.payments){const row=el('div',undefined,'payment-row'),left=el('div',p.method);left.append(el('small',num(p.orders)+' طلب'));row.append(left,el('strong',money(p.amount,p.currency)));pay.append(row)}if(!d.payments.length)empty(pay);
 const stages=[['sessions','جلسة تصفح'],['product','شاهد كتابًا'],['cart','أضاف للسلة'],['checkout','بدأ الدفع'],['paid','أتم شراءً مؤكدًا']];$('journeyFunnel').replaceChildren();for(const [key,label]of stages){const item=el('div',undefined,'funnel-step');item.append(el('b',label),el('strong',num(d.funnel[key])),el('small','جلسات فريدة'));$('journeyFunnel').append(item)}
 $('sourcesBody').replaceChildren();for(const s of d.sources){const row=el('tr');for(const value of [sources[s.source]||s.source,mediums[s.medium]||s.medium,num(s.sessions),num(s.carts),num(s.checkouts),num(s.paid)])row.append(el('td',value));$('sourcesBody').append(row)}if(!d.sources.length){const row=el('tr'),cell=el('td','لا توجد جلسات مسجلة خلال الفترة.','empty');cell.colSpan=6;row.append(cell);$('sourcesBody').append(row)}
 $('trackingNotice').textContent='قياس داخلي مستقل يبدأ من '+(d.trackingSince?when(d.trackingSince):'وقت تفعيل اللوحة')+'. لا يشمل زيارات سابقة، وقد تختلف أرقامه عن Google Analytics. الجلسة تنتهي بعد 30 دقيقة، والاحتفاظ بتفاصيلها 180 يومًا.';$('unattributedNotice').textContent='طلبات مدفوعة بلا مصدر مسجل خلال الفترة: '+num(Math.max(0,d.paidOrders-d.attributedPaidOrders))+'. لا ننسب هذه الطلبات إلى إعلان أو زيارة مباشرة بدون بيانات.';renderOrders();chart();}
async function load(){if(busy)return;busy=true;$('refreshButton').disabled=true;message('dashboardMessage','');$('filters').classList.toggle('busy',true);$('orderFilters').classList.toggle('busy',true);const params=new URLSearchParams({from:$('dateFrom').value,to:$('dateTo').value,country:$('countryFilter').value,status:$('statusFilter').value,query:$('orderQuery').value.trim(),offset:String(offset)});try{data=await api('report?'+params);authenticated(true);render()}catch(e){if(e.status===403){authenticated(false);message('loginMessage','انتهت جلسة الدخول. اطلب رمزًا جديدًا.')}else message('dashboardMessage',e.message)}finally{busy=false;$('refreshButton').disabled=false;$('filters').classList.toggle('busy',false);$('orderFilters').classList.toggle('busy',false)}}
$('emailForm').addEventListener('submit',async e=>{e.preventDefault();$('requestCode').disabled=true;message('loginMessage','');try{const d=await api('request',{email:$('adminEmail').value});challenge=d.challenge;$('emailForm').hidden=true;$('codeForm').hidden=false;$('loginCode').focus()}catch(e){message('loginMessage',e.message)}finally{$('requestCode').disabled=false}});
$('codeForm').addEventListener('submit',async e=>{e.preventDefault();$('verifyCode').disabled=true;try{await api('verify',{challenge,code:$('loginCode').value});$('loginCode').value='';$('adminEmail').value='';await load()}catch(e){message('loginMessage',e.message)}finally{$('verifyCode').disabled=false}});
$('changeEmail').addEventListener('click',()=>{$('codeForm').hidden=true;$('emailForm').hidden=false;$('loginCode').value='';challenge='';message('loginMessage','')});
$('filters').addEventListener('submit',e=>{e.preventDefault();offset=0;load()});$('orderFilters').addEventListener('submit',e=>{e.preventDefault();offset=0;load()});$('chartCurrency').addEventListener('change',chart);
document.querySelectorAll('[data-days]').forEach(b=>b.addEventListener('click',()=>{setRange(Number(b.dataset.days));offset=0;load()}));document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>selectView(b.dataset.view)));
$('previousPage').addEventListener('click',()=>{offset=Math.max(0,offset-20);load()});$('nextPage').addEventListener('click',()=>{offset+=20;load()});$('showOrdersButton').addEventListener('click',()=>selectView('orders'));
$('logoutButton').addEventListener('click',async()=>{try{await api('logout',{});location.replace('/admin')}catch(e){message('dashboardMessage',e.message)}});
setRange(30);$('dateTo').max=new Date(Date.now()+3*3600000).toISOString().slice(0,10);selectView('overview');api('session').then(()=>load()).catch(e=>{authenticated(false);if(e.status!==403)message('loginMessage',e.message)});
})();
