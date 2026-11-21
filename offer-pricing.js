/* Exact minor-unit pricing for the server-defined curated packs. */
var TaboOfferPricing = (() => {
  'use strict';
  function best(ids, config) {
    const minor = value => Math.round(Number(value) * 100);
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
      let savings = 0, overlap = false;
      for (let i = 0; i < eligible.length; i++) if (mask & (1 << i)) {
        const offer = eligible[i];
        if (offer.books.some(id => used.has(id))) { overlap = true; break; }
        offer.books.forEach(id => used.add(id));
        const packFixed = offer.books.filter(fixed);
        const standalone = regular(offer.books.length - packFixed.length) + packFixed.reduce((sum,id) => sum + minor(config.bookPolicies[id].price),0);
        applied.push(offer.id); savings += standalone - minor(offer.price);
      }
      if (overlap) continue;
      const cost = baseline - savings;
      if (cost <= 0) continue;
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
