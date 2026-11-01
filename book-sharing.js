/* Share only canonical public product links; never the current query/session. */
(function () {
    'use strict';
    let language = 'ar', publicURL = '', title = '', busy = false;
    const node = id => document.getElementById(id);
    const copyText = () => language === 'ar' ? 'نسخ رابط الكتاب' : 'Copy book link';
    async function copy() {
        if (busy) return;
        busy = true; node('copyBookLink').disabled = true;
        try {
            if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('Clipboard unavailable');
            await navigator.clipboard.writeText(publicURL);
            node('bookShareStatus').textContent = language === 'ar' ? 'تم نسخ رابط الكتاب.' : 'Book link copied.';
        } catch (_) {
            const input = node('bookShareURL'); input.hidden = false; input.value = publicURL; input.focus(); input.select();
            node('bookShareStatus').textContent = language === 'ar' ? 'يمكنك نسخ الرابط من الخانة الظاهرة.' : 'You can copy the link from the field below.';
        } finally { busy = false; node('copyBookLink').disabled = false; }
    }
    function render(data) {
        if (!/^book[1-9][0-9]*$/.test(data.id)) return;
        language = data.language === 'en' ? 'en' : 'ar'; title = String(data.title);
        publicURL = 'https://tabobooks.com/books/' + data.id;
        node('bookShareHeading').textContent = language === 'ar' ? 'شارك الكتاب' : 'Share this book';
        node('shareWhatsApp').textContent = language === 'ar' ? 'مشاركة عبر واتساب' : 'Share on WhatsApp';
        node('shareWhatsApp').href = 'https://wa.me/?text=' + encodeURIComponent(title + '\n' + publicURL);
        node('copyBookLink').textContent = copyText(); node('copyBookLink').onclick = copy;
        node('bookShareURL').setAttribute('aria-label', language === 'ar' ? 'رابط الكتاب' : 'Book link');
        node('bookShareURL').value = publicURL;
    }
    window.TaboBookSharing = {render};
})();
