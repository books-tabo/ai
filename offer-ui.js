/* Public offer labels and ad-parameter continuity; never creates an order. */
var TaboOfferUI = (() => {
  'use strict';
  function cartUrl(id, search) {
    const params = new URLSearchParams(search);
    params.set('offer', id); params.set('cart', '1');
    return '/?' + params.toString();
  }
  function money(value, currency, ar) {
    return new Intl.NumberFormat(ar ? 'ar-EG' : 'en-US', {style:'currency',currency,maximumFractionDigits:2}).format(value);
  }
  function render(config, language, cartIds = []) {
    const ar = language !== 'en', selected = new Set(cartIds);
    document.querySelectorAll('[data-offer-section-title]').forEach(el => { el.textContent = ar ? 'باقات كتب مختارة' : 'Curated book packs'; });
    document.querySelectorAll('[data-offer-id]').forEach(card => {
      const id = card.dataset.offerId, offer = TABO_CURATED_OFFERS[id];
      if (!offer) return;
      const active = (config.offers || []).find(item => item.id === id);
      const count = card.querySelector('[data-offer-count]');
      if (count) count.textContent = offer.books.length + (ar ? ' كتب PDF' : ' PDF books');
      const title = card.querySelector('[data-offer-title]');
      if (title) title.textContent = offer.title[ar ? 'ar' : 'en'];
      const description = card.querySelector('[data-offer-description]');
      if (description) description.textContent = offer.description[ar ? 'ar' : 'en'];
      const price = card.querySelector('[data-offer-price]');
      if (price) price.textContent = active ? money(active.price,config.currency,ar) : Object.entries(offer.prices).map(([currency,value]) => money(value,currency,ar)).join(' / ');
      const original = card.querySelector('[data-offer-original]');
      const originalPrice = offer.fixedPriceBundle ? offer.books.reduce((total,id) => total + Number(config.bookPolicies?.[id]?.price || 0),0) : config.prices?.[offer.books.length];
      if (original) { original.hidden = !active || !(originalPrice > active.price); original.textContent = active ? (ar ? 'بدلًا من ' : 'Regular total ')+money(originalPrice,config.currency,ar) : ''; }
      const availability = card.querySelector('[data-offer-availability]');
      if (availability) availability.textContent = Object.keys(offer.prices).length === 1 && offer.prices.USD ? (ar ? 'هذا العرض متاح عند التسعير بالدولار فقط. داخل مصر يظل سعر كل كتاب 500 جنيه.' : 'This offer applies only to USD pricing. In Egypt each book remains EGP 500.') : '';
      card.querySelectorAll('[data-offer-buy]').forEach(el => { el.href = cartUrl(id,location.search); });
      card.querySelectorAll('[data-offer-buy],[data-add-offer]').forEach(el => { el.hidden = Boolean(config.currency && !active); el.disabled = !active; el.setAttribute('aria-disabled',String(!active)); });
      card.querySelectorAll('[data-offer-button-label]').forEach(el => { el.textContent = offer.books.every(b => selected.has(b)) ? (ar ? 'عرض الباقة في السلة' : 'View pack in cart') : (ar ? 'أضف الباقة للسلة' : 'Add pack to cart'); });
      card.querySelectorAll('[data-offer-details]').forEach(el => { el.textContent = ar ? 'تفاصيل الباقة والكتب' : 'Pack details and books'; });
    });
    document.querySelectorAll('[data-offer-book-name]').forEach(el => {
      const book = TABO_OFFER_BOOK_NAMES[el.dataset.offerBookName];
      if (book) el.textContent = book[ar ? 'ar' : 'en'];
    });
    document.querySelectorAll('[data-offers-label]').forEach(el => { el.textContent = ar ? 'عروض الباقات' : 'Book pack offers'; });
  }
  return {render,cartUrl};
})();
