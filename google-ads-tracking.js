/* Google Ads: global attribution tag; conversions only on an authenticated receipt. */
(() => {
    'use strict';
    const tag = document.currentScript;
    const tagId = tag && tag.dataset.googleTagId;
    const label = tag && tag.dataset.googleConversionLabel;
    if (!/^AW-\d{5,25}$/.test(tagId || '') || !/^[A-Za-z0-9_-]+$/.test(label || '') ||
        !['tabobooks.com', 'www.tabobooks.com'].includes(location.hostname) || window.TaboGoogleAds) return;
    const receipt = location.pathname === '/success.html';
    const destination = tagId + '/' + label;
    const purchases = new Set();
    const attribution = new Set(['gclid', 'dclid', 'gclsrc', 'gbraid', 'wbraid', 'gad_source', 'gad_campaignid',
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id', 'fbclid']);
    let loading;

    function safeUrl(raw) {
        if (!raw) return '';
        try {
            const url = new URL(raw);
            if (!['https:', 'http:'].includes(url.protocol)) return '';
            url.username = ''; url.password = ''; url.hash = '';
            for (const key of [...url.searchParams.keys()]) {
                if (!attribution.has(key.toLowerCase())) url.searchParams.delete(key);
            }
            return url.href;
        } catch (_) { return ''; }
    }

    function load() {
        if (loading) return loading;
        loading = new Promise(resolve => {
            window.dataLayer = window.dataLayer || [];
            window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
            window.gtag('js', new Date());
            window.gtag('config', tagId, {
                page_location: safeUrl(location.href), page_referrer: safeUrl(document.referrer)
            });
            const script = document.createElement('script');
            script.async = true;
            script.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(tagId);
            script.nonce = tag.nonce;
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        });
        return loading;
    }

    async function purchase(data) {
        if (!receipt || location.search || location.hash || !data ||
            !/^[a-f0-9]{64}$/.test(data.transaction_id || '') ||
            !['EGP', 'USD'].includes(data.currency) || !Number.isFinite(data.value) || data.value <= 0) return;
        const transaction = data.transaction_id;
        const key = 'taboGoogleAdsPurchase:' + destination + ':' + transaction;
        try { if (localStorage.getItem(key)) return; } catch (_) { /* Storage may be disabled. */ }
        if (purchases.has(transaction)) return;
        purchases.add(transaction);
        try {
            if (!await load()) { purchases.delete(transaction); return; }
            window.gtag('event', 'conversion', {
                send_to: destination, value: data.value, currency: data.currency,
                transaction_id: transaction,
                page_location: safeUrl(location.href), page_referrer: safeUrl(document.referrer),
                event_callback: () => {
                    try { localStorage.setItem(key, '1'); } catch (_) { /* Google also deduplicates the transaction ID. */ }
                }, event_timeout: 2000
            });
        } catch (_) { purchases.delete(transaction); /* Tracking must not block book access. */ }
    }

    window.TaboGoogleAds = {purchase};
    // Receipt URLs can contain protected order links: wait for authenticated data and URL cleanup.
    if (!receipt) load();
})();
