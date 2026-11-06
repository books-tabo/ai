/* Snap Pixel: no customer matching fields; purchase requires an authenticated receipt. */
(() => {
    'use strict';
    if (window.TaboSnapTracking) return;
    const tag = document.currentScript;
    const pixelId = tag && tag.dataset.snapPixelId;
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(pixelId || '') ||
        !['tabobooks.com', 'www.tabobooks.com'].includes(location.hostname)) return;
    const receipt = location.pathname === '/success.html';
    const purchases = new Set();
    let loading;

    function load() {
        if (loading) return loading;
        loading = new Promise(resolve => {
            if (!window.snaptr) {
                const queue = function () {
                    queue.handleRequest ? queue.handleRequest.apply(queue, arguments) : queue.queue.push(arguments);
                };
                queue.queue = [];
                window.snaptr = queue;
            }
            window.snaptr('init', pixelId, {});
            const script = document.createElement('script');
            script.async = true;
            script.src = 'https://sc-static.net/scevent.min.js';
            script.nonce = tag.nonce;
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        });
        return loading;
    }

    function properties(data) {
        const ids = data && Array.isArray(data.content_ids) ? data.content_ids : [];
        if (!ids.length || !ids.every(id => /^book[1-9][0-9]*$/.test(id)) ||
            !['EGP', 'USD'].includes(data.currency) || !Number.isFinite(data.value) || data.value <= 0) return null;
        return {item_ids: ids, number_items: ids.length, price: data.value, currency: data.currency};
    }

    async function track(name, data = {}) {
        if (receipt || !['PageView', 'ViewContent', 'AddToCart', 'InitiateCheckout'].includes(name)) return;
        const event = {PageView: 'PAGE_VIEW', ViewContent: 'VIEW_CONTENT',
            AddToCart: 'ADD_CART', InitiateCheckout: 'START_CHECKOUT'}[name];
        const safe = name === 'PageView' ? {} : properties(data);
        try { if (safe && await load()) window.snaptr('track', event, safe); }
        catch (_) { /* Advertising delivery must not affect shopping. */ }
    }

    async function purchase(data) {
        if (!receipt || location.search || location.hash || !data ||
            !/^[a-f0-9]{64}$/.test(data.transaction_id || '')) return;
        const safe = properties(data);
        if (!safe || purchases.has(data.transaction_id)) return;
        const key = 'taboSnapPurchase:' + pixelId + ':' + data.transaction_id;
        try { if (localStorage.getItem(key)) return; } catch (_) { /* Storage may be disabled. */ }
        purchases.add(data.transaction_id);
        try {
            if (!await load()) { purchases.delete(data.transaction_id); return; }
            window.snaptr('track', 'PURCHASE', {...safe, transaction_id: data.transaction_id,
                client_dedup_id: data.transaction_id});
        } catch (_) { purchases.delete(data.transaction_id); return; }
        try { localStorage.setItem(key, '1'); } catch (_) { /* Same-document deduplication remains. */ }
    }

    window.TaboSnapTracking = {track, purchase};
    // Reuse the storefront's existing event calls, including when Meta is disabled.
    const previous = window.TaboTracking || {};
    window.TaboTracking = {...previous, track(name, data) {
        try { previous.track?.(name, data)?.catch?.(() => {}); } catch (_) { /* Keep deliveries independent. */ }
        return track(name, data);
    }};
    if (!receipt) track('PageView');
})();
