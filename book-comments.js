/* Real reader comments. No tracking calls, email persistence or HTML rendering. */
(()=>{'use strict';
const root=document.getElementById('bookComments');
const match=location.pathname.match(/^\/books\/(book[1-9][0-9]*)$/);
if(!root||!match)return;
const bookId=match[1],el=id=>document.getElementById(id);
const copy={
 ar:{title:'تعليقات القرّاء',policy:'شارك رأيك الحقيقي في الكتاب. نراجع التعليقات لمنع الإساءة والرسائل المزعجة، سواء كان الرأي إيجابيًا أو سلبيًا.',name:'الاسم المعروض (اختياري)',email:'البريد الإلكتروني',body:'تعليقك عن الكتاب',privacy:'بريدك لن يظهر للزوار. لا تضع بياناتك الشخصية داخل التعليق.',consent:'أوافق على نشر اسمي المعروض وتعليقي بعد المراجعة وفق سياسة الخصوصية.',privacyLink:'سياسة الخصوصية',submit:'إرسال التعليق للمراجعة',sending:'جارٍ الإرسال…',pending:'تم استلام تعليقك للمراجعة. سيظهر بعد اعتماده. شكرًا لمشاركة رأيك.',error:'تعذر إرسال التعليق. تأكد من البيانات وحاول مرة أخرى.',limited:'طلبات كثيرة. حاول مرة أخرى بعد قليل.',empty:'لا توجد تعليقات منشورة بعد. شارك تجربتك الحقيقية مع الكتاب.',loading:'جارٍ تحميل التعليقات…',unavailable:'تعذر تحميل التعليقات الآن. حاول لاحقًا.',more:'عرض المزيد',verified:'شراء موثّق',reader:'قارئ'},
 en:{title:'Reader comments',policy:'Share your honest opinion of the book. Comments are reviewed for abuse and spam, whether positive or negative.',name:'Display name (optional)',email:'Email address',body:'Your comment about the book',privacy:'Your email will not be displayed. Do not include private contact details in your comment.',consent:'I agree to publication of my display name and comment after moderation under the privacy policy.',privacyLink:'Privacy policy',submit:'Submit comment for review',sending:'Sending…',pending:'Your comment has been received for moderation and will appear once approved. Thank you for sharing your opinion.',error:'Could not submit your comment. Check the details and try again.',limited:'Too many requests. Please try again later.',empty:'No published comments yet. Share your real experience with the book.',loading:'Loading comments…',unavailable:'Comments could not be loaded. Please try again later.',more:'Show more',verified:'Verified purchase',reader:'Reader'}
};
let rows=[],total=0,offset=0,busy=false,loading=true,loadError=false,state='';
function english(){return document.documentElement.lang==='en'}
function t(){return copy[english()?'en':'ar']}
function render(){
 const c=t();
 const labels={commentsPolicy:c.policy,commentNameLabel:c.name,commentEmailLabel:c.email,commentBodyLabel:c.body,commentPrivacy:c.privacy,commentConsentText:c.consent,commentPrivacyLink:c.privacyLink,commentsMore:c.more};
 for(const [id,value] of Object.entries(labels))el(id).textContent=value;
 el('commentsTitle').textContent=c.title+(total?` (${total})`:'');
 el('commentSubmit').textContent=busy?c.sending:c.submit;
 el('commentSubmit').disabled=busy;
 el('commentStatus').textContent=state?c[state]:'';
 const list=el('commentsList');list.replaceChildren();
 if(!rows.length){const note=document.createElement('p');note.textContent=loading?c.loading:loadError?c.unavailable:c.empty;list.append(note)}
 for(const row of rows){
  const item=document.createElement('article');item.className='reader-comment';
  const heading=document.createElement('header'),name=document.createElement('strong');
  name.textContent=row.display_name==='قارئ'?c.reader:row.display_name;heading.append(name);
  if(row.verified_purchase===true){const badge=document.createElement('span');badge.className='comment-verified';badge.textContent=c.verified;heading.append(badge)}
  const time=document.createElement('time');time.dateTime=row.created_at;
  const date=new Date(row.created_at);if(!Number.isNaN(date.getTime()))time.textContent=new Intl.DateTimeFormat(english()?'en':'ar',{dateStyle:'medium'}).format(date);
  heading.append(time);
  const body=document.createElement('p');body.textContent=row.body;item.append(heading,body);list.append(item);
 }
 el('commentsMore').hidden=loading||rows.length>=total||offset>=1000;
 el('commentsMore').disabled=loading;
}
async function load(){
 loading=true;loadError=false;render();
 try{
  const response=await fetch(`/api/book-comments?book_id=${encodeURIComponent(bookId)}&offset=${offset}`,{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new Error('Comments unavailable');
  const data=await response.json();if(!Array.isArray(data.comments)||!Number.isSafeInteger(data.total)||data.total<0)throw new Error('Invalid response');
  const seen=new Set(rows.map(row=>row.id));for(const row of data.comments)if(!seen.has(row.id)){rows.push(row);seen.add(row.id)}
  total=data.total;offset+=data.comments.length;
 }catch(_){loadError=true}finally{loading=false;render()}
}
el('commentsMore').addEventListener('click',load);
el('commentForm').addEventListener('submit',async event=>{
 event.preventDefault();if(busy||!el('commentForm').reportValidity())return;
 busy=true;state='';render();
 // The order ID is only a hint: the server requires the existing paid, device-bound
 // HttpOnly session before awarding a purchase badge. No order IDs are published.
 const payload={book_id:bookId,email:el('commentEmail').value,display_name:el('commentName').value,
  body:el('commentBody').value,consent:el('commentConsent').checked,website:el('commentWebsite').value};
 try{const previous=localStorage.getItem('taboLastOrderId');if(previous)payload.order_id=previous}catch(_){}
 try{
  const response=await fetch('/api/book-comments',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!response.ok){state=response.status===429?'limited':'error';return}
  const data=await response.json();if(data.ok!==true||data.status!=='pending')throw new Error('Comment not accepted');
  state='pending';el('commentForm').reset();
 }catch(_){state='error'}finally{busy=false;render()}
});
new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
render();load();
})();
