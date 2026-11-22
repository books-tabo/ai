"""Advertised totals, mixed carts and crawlable public offer pages."""
import unittest
from decimal import Decimal
from unittest.mock import patch

import catalog
import localization
import seo
from support import HTTPCase
from urllib.request import Request
from urllib.error import HTTPError


class CuratedPricingTests(unittest.TestCase):
    def test_all_advertised_packs_cost_less_than_the_same_quantity_of_books(self):
        from itertools import permutations
        expected={'sigma-couple':(279.99,7.99),'change-starter':(409.99,10.99),
                  'money-and-wealth':(539.99,13.99),'self-growth':(669.99,16.99),
                  'sigma-trilogy':(539.99,13.99),'marriage-awareness':(409.99,10.99)}
        self.assertEqual(set(catalog.OFFERS),set(expected))
        for oid,offer in catalog.OFFERS.items():
            self.assertEqual(set(offer['prices']),{'EGP','USD'})
            for country,total in [('EG',expected[oid][0]),('SA',expected[oid][1])]:
                config=localization.pricing_for_country(catalog.PRICES,country,at=0)
                self.assertLess(total,config['prices'][str(len(offer['books']))])
                for ids in permutations(offer['books']):
                    result=catalog.pricing_breakdown(list(ids),config)
                    self.assertEqual(result['total'],Decimal(str(total)))
                    self.assertEqual(result['offerIds'],[oid])

    def test_disjoint_packs_and_uncovered_books_keep_the_extra_book_discount(self):
        starter=catalog.OFFERS['change-starter']['books']
        money=catalog.OFFERS['money-and-wealth']['books']
        growth=catalog.OFFERS['self-growth']['books']
        cases=[(starter+money,948.98,23.98),(growth+money,1208.98,29.98),
               (['book20','book21','book1'],428.98,11.98),
               (['book20','book21','book22','book23','book1'],688.98,17.98),
               (['book24','book23','book22','book1'],558.98,14.98),
               (['book20','book21','book22','book23','book24'],688.98,17.98),
               (growth[:-1],558.98,14.98),(starter[:-1],298.98,8.98),
               (list(catalog.BOOKS),3535.85,90.85)]
        for ids,egp,usd in cases:
            for country,expected in [('EG',egp),('SA',usd)]:
                config=localization.pricing_for_country(catalog.PRICES,country,at=0)
                self.assertEqual(catalog.total_for_items(ids,config),expected,(country,ids))
                result=catalog.pricing_breakdown(ids,config)
                used=[]
                for oid in result['offerIds']:used+=catalog.OFFERS[oid]['books']
                self.assertEqual(len(used),len(set(used)))
                self.assertGreater(result['total'],0)
                self.assertLessEqual(result['total'],Decimal(str(config['prices'][str(len(ids))])))

    def test_removing_a_required_book_removes_the_pack_and_duplicates_are_rejected(self):
        for country in ('EG','SA'):
            config=localization.pricing_for_country(catalog.PRICES,country,at=0)
            for oid,offer in catalog.OFFERS.items():
                for removed in offer['books']:
                    ids=[bid for bid in offer['books'] if bid!=removed]
                    self.assertNotIn(oid,catalog.pricing_breakdown(ids,config)['offerIds'])
            with self.assertRaises(ValueError):catalog.total_for_items(['book20','book20'],config)


class OfferPageTests(HTTPCase):
    def get(self,path):
        request=Request(self.base+path,headers={'Host':'tabobooks.com','User-Agent':'SyntheticBrowser/1'})
        try:r=self.opener.open(request,timeout=10)
        except HTTPError as error:r=error
        return r.status,r.headers,r.read().decode('utf-8')

    # Reuse the HTTP harness, without inheriting the unrelated SEO tests twice.
    def test_public_offer_pages_show_only_the_actual_books_and_prices(self):
        for slug,offer in catalog.OFFERS.items():
            status,headers,source=self.get('/offers/'+slug+'?utm_source=facebook&fbclid=synthetic')
            self.assertEqual(status,200);self.assertNotIn('noindex',headers.get('X-Robots-Tag',''))
            self.assertIn('href="https://tabobooks.com/offers/'+slug+'"',source)
            self.assertIn(offer['title']['ar'],source)
            for currency,value in offer['prices'].items():
                self.assertIn(str(value)+' جنيه' if currency=='EGP' else '$'+str(value),source)
            self.assertTrue(all('href="/books/'+bid+'"' in source for bid in offer['books']))
            self.assertIn('data-offer-buy',source)
            if not offer.get('fixedPriceBundle'):
                self.assertNotIn('href="/books/book20"',source)
        self.assertEqual(self.get('/offers/unknown')[0],404)
        offer_paths = {path for path in seo.public_paths() if path == '/offers' or path.startswith('/offers/')}
        self.assertEqual(offer_paths, {'/offers'} | {'/offers/' + slug for slug in catalog.OFFERS})
        home=self.get('/')[2]
        self.assertIn('href="/offers"',home)
        self.assertNotIn('id="curatedOffers"',home)
        listing=self.get('/offers')[2]
        for slug in catalog.OFFERS:
            self.assertIn('data-offer-id="'+slug+'"',listing)
        self.assertIn('height:auto',home)
        self.assertIn('id="cartOfferSummary"',self.get('/checkout')[2])

    def test_sigma_detail_explains_each_book_before_the_purchase_action(self):
        for language in ('ar', 'en'):
            with self.subTest(language=language):
                body = seo.page_data('/offers/sigma-trilogy')[language]['body']
                summary = body.split('</article>', 1)[0]
                positions = [summary.index('class="sigma-offer-intro"')]
                positions += [summary.index('href="/books/' + bid + '"') for bid in
                              ('book20', 'book21', 'book22', 'book23')]
                positions += [summary.index('data-offer-price'), summary.index('data-offer-buy')]
                self.assertEqual(positions, sorted(positions))
                self.assertEqual(summary.count('<li>'), 4)
                self.assertEqual(summary.count('<span>'), 4)
                self.assertIn('href="/?offer=sigma-trilogy&amp;cart=1"', summary)
        self.assertIn('أربعة كتب لفهم الرغبة والجسد والتواصل بين الزوجين', self.get('/offers/sigma-trilogy')[2])
        for path in ('/', '/offers', '/offers/sigma-couple'):
            self.assertNotIn('sigma-offer-detail', self.get(path)[2])
