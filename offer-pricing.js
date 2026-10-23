/* Exact minor-unit pricing for the server-defined curated packs. */
var TaboOfferPricing = (() => {
  'use strict';
  function best(ids, config) {
    const minor = value => Math.round(Number(value) * 100);
    const selected = new Set(ids);
    if (selected.size !== ids.length) throw Error('Duplicate books');
    const regular = count => count ? minor(config.prices[count]) : 0;
    let totalMinor = regular(ids.length), offerIds = [];
    const eligible = (config.offers || []).filter(offer => offer.books.every(id => selected.has(id)));
    for (let mask = 1; mask < (1 << eligible.length); mask++) {
      const used = new Set(), applied = [];
      let cost = 0, overlap = false;
      for (let i = 0; i < eligible.length; i++) if (mask & (1 << i)) {
        const offer = eligible[i];
        if (offer.books.some(id => used.has(id))) { overlap = true; break; }
        offer.books.forEach(id => used.add(id));
        applied.push(offer.id); cost += minor(offer.price);
      }
      if (overlap) continue;
      cost += regular(ids.length - used.size);
      if (cost < totalMinor) { totalMinor = cost; offerIds = applied; }
    }
    return {totalMinor, offerIds, savingsMinor: regular(ids.length) - totalMinor};
  }
  return {best};
})();
