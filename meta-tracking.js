/* Public Meta Pixel; no customer fields, automatic matching or preview tracking. */
(() => {
    'use strict';
    const tag = document.currentScript;
    const pixelId = tag && tag.dataset.pixelId;
    if (!/^\d{5,25}$/.test(pixelId || '') ||
        !['tabobooks.com', 'www.tabobooks.com'].includes(location.hostname)) return;
    const receipt = location.pathname === '/success.html';
    let loading;
    const purchases = new Set();

    function load() {
        if (loading) return loading;
        loading = new Promise(resolve => {
            if (!window.fbq) {
                const queue = function () {
                    queue.callMethod ? queue.callMethod.apply(queue, arguments) : queue.queue.push(arguments);
                };
                window.fbq = queue;
                if (!window._fbq) window._fbq = queue;
                queue.push = queue; queue.loaded = true; queue.version = '2.0'; queue.queue = [];
            }
            window.fbq('set', 'autoConfig', false, pixelId);
            window.fbq('init', pixelId);
            const script = document.createElement('script');
            script.async = true;
            script.src = 'https://connect.facebook.net/en_US/fbevents.js';
            script.nonce = tag.nonce;
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        });
        return loading;
    }

    function properties(data) {
        const ids = Array.isArray(data.content_ids) ? data.content_ids : [];
        if (!ids.length || !ids.every(id => /^book(?:[1-9]|1[0-8])$/.test(id)) ||
            !['EGP', 'USD'].includes(data.currency) || !Number.isFinite(data.value) || data.value <= 0) return null;
        return {content_ids: ids, content_type: 'product', num_items: ids.length,
                value: data.value, currency: data.currency};
    }

    async function track(name, data = {}) {
        if (receipt || !['PageView', 'ViewContent', 'AddToCart', 'InitiateCheckout'].includes(name)) return;
        const safe = name === 'PageView' ? {} : properties(data);
        if (safe && await load()) window.fbq('trackSingle', pixelId, name, safe);
    }

    async function purchase(data) {
        if (!receipt || location.search || location.hash || !data || !/^[a-f0-9]{64}$/.test(data.eventID || '')) return;
        const safe = properties(data);
        if (!safe || purchases.has(data.eventID)) return;
        const key = 'taboMetaPurchase:' + pixelId + ':' + data.eventID;
        try { if (localStorage.getItem(key)) return; } catch (_) { /* Storage may be disabled. */ }
        purchases.add(data.eventID);
        if (!await load()) { purchases.delete(data.eventID); return; }
        window.fbq('trackSingle', pixelId, 'Purchase', safe, {eventID: data.eventID});
        try { localStorage.setItem(key, '1'); } catch (_) { /* The event ID remains stable. */ }
    }

    window.TaboTracking = {track, purchase};
    if (!receipt) track('PageView');
})();
