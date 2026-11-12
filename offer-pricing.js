/* Exact minor-unit pricing for the server-defined curated packs. */
var TaboOfferPricing = (() => {
  'use strict';
  function best(ids, config) {
    const scale = config.currency === 'KWD' ? 1000 : 100;
    const minor = value => Math.round(Number(value) * scale);
    const selected = new Set(ids);
    if (selected.size !== ids.length) throw Error('Duplicate books');
    const regular = count => count ? minor(config.prices[count]) : 0;
    const fixed = id => config.bookPolicies?.[id]?.bundleEligible === false;
    function remainder(used) {
      const remaining = ids.filter(id => !used.has(id));
      return regular(remaining.filter(id => !fixed(id)).length) +
        remaining.filter(fixed).reduce((total,id) => total + minor(config.bookPolicies[id].price), 0);
    }
    const baseline = remainder(new Set());
    let totalMinor = baseline, offerIds = [], covered = new Set();
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
      cost += remainder(used);
      if (cost < totalMinor || (cost === totalMinor && offerIds.length && used.size > covered.size)) {
        totalMinor = cost; offerIds = applied; covered = used;
      }
    }
    const savingsMinor = baseline - totalMinor;
    const promotion = config.bookPromotion;
    const promoted = promotion?.bookId;
    if (promotion?.currency === config.currency && selected.has(promoted) && fixed(promoted) && !covered.has(promoted)) {
      totalMinor -= minor(config.bookPolicies[promoted].price) - minor(promotion.price);
    }
    return {totalMinor, offerIds, savingsMinor};
  }
  return {best};
})();
