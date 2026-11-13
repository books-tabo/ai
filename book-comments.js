/* Real reader comments. No tracking calls, browser email persistence or HTML rendering. */
(()=>{'use strict';
const root=document.getElementById('bookComments');
const match=location.pathname.match(/^\/books\/(book[1-9][0-9]*)$/);
if(!root||!match)return;
const bookId=match[1],el=id=>document.getElementById(id);
const copy={
 ar:{rating:'تقييمك للكتاب',outOfFive:'من ٥ نجوم',justNow:'منذ لحظات',title:'تعليقات القرّاء',policy:'شارك رأيك الحقيقي في الكتاب. يُنشر التعليق فور إرساله، وقد تُزال التعليقات المسيئة أو المزعجة.',name:'الاسم المعروض (اختياري)',email:'البريد الإلكتروني',body:'تعليقك عن الكتاب',privacy:'بريدك لن يظهر للزوار. لا تضع بياناتك الشخصية داخل التعليق.',consent:'أوافق على نشر اسمي المعروض وتقييمي وتعليقي فورًا وفق سياسة الخصوصية.',privacyLink:'سياسة الخصوصية',updates:'أرغب في تلقي عروض تابو بوكس وأخبار إصدارات الكتب عبر البريد الإلكتروني (اختياري).',submit:'نشر التعليق',sending:'جارٍ الإرسال…',accepted:'شكرًا لمشاركة رأيك.',error:'تعذر إرسال التعليق. تأكد من البيانات وحاول مرة أخرى.',limited:'طلبات كثيرة. حاول مرة أخرى بعد قليل.',empty:'لا توجد تعليقات منشورة بعد. شارك تجربتك الحقيقية مع الكتاب.',loading:'جارٍ تحميل التعليقات…',unavailable:'تعذر تحميل التعليقات الآن. حاول لاحقًا.',more:'عرض المزيد',verified:'شراء موثّق',reader:'قارئ'},
 en:{rating:'Your book rating',outOfFive:'out of 5 stars',justNow:'Just now',title:'Reader comments',policy:'Share your honest opinion of the book. Comments publish immediately. Abusive comments or spam may be removed.',name:'Display name (optional)',email:'Email address',body:'Your comment about the book',privacy:'Your email will not be displayed. Do not include private contact details in your comment.',consent:'I agree to immediate publication of my display name, rating and comment under the privacy policy.',privacyLink:'Privacy policy',updates:'I would like to receive TABO BOOKS offers and new-release updates by email (optional).',submit:'Publish comment',sending:'Sending…',accepted:'Thank you for sharing your opinion.',error:'Could not submit your comment. Check the details and try again.',limited:'Too many requests. Please try again later.',empty:'No published comments yet. Share your real experience with the book.',loading:'Loading comments…',unavailable:'Comments could not be loaded. Please try again later.',more:'Show more',verified:'Verified purchase',reader:'Reader'}
};
let rows=[],total=0,offset=0,busy=false,loading=true,loadError=false,state='',loadVersion=0;
function english(){return document.documentElement.lang==='en'}
function t(){return copy[english()?'en':'ar']}
function selectedRating(){
 const selected=el('commentRating').querySelector('input:checked');
 return selected?Number(selected.value):null;
}
function renderRatingChoice(){
 const rating=selectedRating();
 for(const input of el('commentRating').querySelectorAll('input')){
  input.setAttribute('aria-label',input.value+' '+t().outOfFive);
  input.parentElement.classList.toggle('is-selected',rating!==null&&Number(input.value)<=rating);
 }
}
function commentDate(value){
 const date=new Date(value);
 if(Number.isNaN(date.getTime()))return '';
 const age=Date.now()-date.getTime();
 if(age<0||age>=30*86400000)return new Intl.DateTimeFormat(english()?'en':'ar',{dateStyle:'medium'}).format(date);
 if(age<60000)return t().justNow;
 let unit='day',duration=86400000;
 if(age<3600000){unit='minute';duration=60000}
 else if(age<86400000){unit='hour';duration=3600000}
 return new Intl.RelativeTimeFormat(english()?'en':'ar',{numeric:'always'}).format(-Math.floor(age/duration),unit);
}
function render(){
 const c=t();
 const labels={commentRatingLabel:c.rating,commentsPolicy:c.policy,commentNameLabel:c.name,commentEmailLabel:c.email,commentBodyLabel:c.body,commentPrivacy:c.privacy,commentConsentText:c.consent,commentUpdatesText:c.updates,commentPrivacyLink:c.privacyLink,commentsMore:c.more};
 for(const [id,value] of Object.entries(labels))el(id).textContent=value;
 el('commentsTitle').textContent=c.title+(total?` (${total})`:'');
 el('commentSubmit').textContent=busy?c.sending:c.submit;
 el('commentSubmit').disabled=busy;
 renderRatingChoice();
 el('commentStatus').textContent=state?c[state]:'';
 const list=el('commentsList');list.replaceChildren();
 if(!rows.length){const note=document.createElement('p');note.textContent=loading?c.loading:loadError?c.unavailable:c.empty;list.append(note)}
 for(const row of rows){
  const item=document.createElement('article');item.className='reader-comment';
  const heading=document.createElement('header');
  const avatar=document.createElement('span');avatar.className='comment-avatar';avatar.setAttribute('aria-hidden','true');
  const author=document.createElement('div');author.className='comment-author';
  const name=document.createElement('strong');name.textContent=row.display_name==='قارئ'?c.reader:row.display_name;author.append(name);
  if(row.verified_purchase===true){const badge=document.createElement('span');badge.className='comment-verified';badge.textContent=c.verified;author.append(badge)}
  const time=document.createElement('time');time.dateTime=row.created_at;time.textContent=commentDate(row.created_at);
  heading.append(avatar,author,time);item.append(heading);
  if(Number.isInteger(row.rating)&&row.rating>=1&&row.rating<=5){
   const rating=document.createElement('div');rating.className='comment-rating';
   rating.setAttribute('role','img');rating.setAttribute('aria-label',row.rating+' '+c.outOfFive);
   const stars=document.createElement('span');stars.className='comment-stars';stars.setAttribute('aria-hidden','true');
   for(let value=1;value<=5;value++){
    const star=document.createElement('span');star.textContent='★';star.className=value<=row.rating?'is-filled':'is-empty';stars.append(star);
   }
   const score=document.createElement('span');score.className='comment-rating-value';score.setAttribute('aria-hidden','true');
   score.textContent=new Intl.NumberFormat(english()?'en':'ar',{minimumFractionDigits:1,maximumFractionDigits:1}).format(row.rating);
   rating.append(score,stars);item.append(rating);
  }
  const body=document.createElement('p');body.textContent=row.body;item.append(body);list.append(item);
 }
 el('commentsMore').hidden=loading||rows.length>=total||offset>=1000;
 el('commentsMore').disabled=loading;
}
async function load(reset=false){
 const version=++loadVersion, start=reset?0:offset;
 loading=true;loadError=false;render();
 try{
  const response=await fetch(`/api/book-comments?book_id=${encodeURIComponent(bookId)}&offset=${start}`,{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new Error('Comments unavailable');
  const data=await response.json();if(!Array.isArray(data.comments)||!Number.isSafeInteger(data.total)||data.total<0)throw new Error('Invalid response');
  if(version!==loadVersion)return;
  if(reset)rows=[];
  const seen=new Set(rows.map(row=>row.id));for(const row of data.comments)if(!seen.has(row.id)){rows.push(row);seen.add(row.id)}
  total=data.total;offset=start+data.comments.length;
 }catch(_){if(version===loadVersion)loadError=true}finally{if(version===loadVersion){loading=false;render()}}
}
el('commentsMore').addEventListener('click',()=>load());
el('commentRating').addEventListener('change',renderRatingChoice);
el('commentForm').addEventListener('submit',async event=>{
 event.preventDefault();if(busy||!el('commentForm').reportValidity())return;
 busy=true;state='';render();
 // The order ID is only a hint: the server requires the existing paid, device-bound
 // HttpOnly session before awarding a purchase badge. No order IDs are published.
 const payload={book_id:bookId,email:el('commentEmail').value,display_name:el('commentName').value,
  body:el('commentBody').value,rating:selectedRating(),consent:el('commentConsent').checked,updates_consent:el('commentUpdates').checked,website:el('commentWebsite').value};
 try{const previous=localStorage.getItem('taboLastOrderId');if(previous)payload.order_id=previous}catch(_){}
 try{
  const response=await fetch('/api/book-comments',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!response.ok){state=response.status===429?'limited':'error';return}
  const data=await response.json();if(data.ok!==true||data.status!=='accepted')throw new Error('Comment not accepted');
  state='accepted';el('commentForm').reset();await load(true);
 }catch(_){state='error'}finally{busy=false;render()}
});
new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
render();load();
})();
