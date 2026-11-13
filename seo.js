/* Public metadata and editorial translations only; no events or navigation. */
(() => {
  'use strict';
  const node = document.getElementById('seo-data');
  if (!node) return;
  const data = JSON.parse(node.textContent);
  // SSR already matches Arabic. Keep links, focus and open FAQ state intact
  // when pricing/statistics rerender the page without a language change.
  let renderedLanguage = document.documentElement.lang === 'en' ? 'en' : 'ar';
  window.TaboSEO = {
    render(language) {
      language = language === 'en' ? 'en' : 'ar';
      const copy = data[language === 'en' ? 'en' : 'ar'];
      document.title = copy.title;
      for (const [selector, value] of [
        ['meta[name="description"]', copy.description],
        ['meta[property="og:title"]', copy.title],
        ['meta[property="og:description"]', copy.description],
        ['meta[property="og:image"]', copy.image],
        ['meta[property="og:locale"]', language === 'en' ? 'en_US' : 'ar_AR']
      ]) document.querySelector(selector)?.setAttribute('content', value);
      document.querySelectorAll('[data-offers-label]').forEach(target => { target.textContent = language === 'en' ? 'Book pack offers' : 'عروض الباقات'; });
      document.querySelectorAll('[data-seo-label="blog"]').forEach(target => { target.textContent = language === 'en' ? 'Articles' : 'المقالات'; });
      if (language === renderedLanguage) return;
      for (const [id, value] of [['seo-breadcrumb', copy.breadcrumb], ['seo-extra', copy.extra], ['seo-body', copy.body]]) {
        const target = document.getElementById(id);
        if (target) target.innerHTML = value;
      }
      document.querySelectorAll('[data-seo-label="categories"]').forEach(target => {
        target.textContent = language === 'en' ? 'Book categories' : 'أقسام الكتب';
      });
      renderedLanguage = language;
    },
    page(language) { return data[language === 'en' ? 'en' : 'ar']; }
  };
})();
