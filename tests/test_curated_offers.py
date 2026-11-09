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
    def test_sigma_pair_is_an_exact_usd_exception_to_independent_prices(self):
        cases=[(['book20'],500,19.99),(['book21'],500,19.99),
               (['book20','book21'],1000,34.99),
               (['book20','book21','book1'],1199,44.98),
               (['book21','book1','book2'],799,37.98),
               (['book20','book21','book18','book14','book6'],1300,49.99)]
        for ids,egp,usd in cases:
            for country,expected in [('EG',egp),('SA',usd)]:
                config=localization.pricing_for_country(catalog.PRICES,country)
                self.assertEqual(catalog.total_for_items(ids,config),expected)
                self.assertEqual(catalog.total_for_items(ids[::-1],config),expected)
        usd=localization.pricing_for_country(catalog.PRICES,'SA')
        self.assertEqual(catalog.pricing_breakdown(['book20','book21'],usd)['offerSavings'],Decimal('4.99'))
        self.assertNotIn('sigma-couple',[o['id'] for o in catalog.public_offers('EGP')])
        for bid,price in [('book20',19.99),('book21',19.99)]:
            self.assertEqual(usd['bookPolicies'][bid],{'price':price,'bundleEligible':False,'previewEnabled':False})

    def test_four_book_pack_is_automatic_and_named_in_either_offer_order(self):
        ids=['book20','book21','book22','book23']
        for country,total,saving in [('EG',1250,'750'),('SA',49,'30.96')]:
            config=localization.pricing_for_country(catalog.PRICES,country)
            for offers in [catalog.OFFERS,dict(reversed(list(catalog.OFFERS.items())))]:
                with patch.object(catalog,'current_offers',return_value=offers):
                    result=catalog.pricing_breakdown(ids,config)
                    self.assertEqual(result,{'total':Decimal(str(total)),'offerIds':['sigma-trilogy'],'offerSavings':Decimal(saving)})
                    for removed in ids:
                        self.assertNotIn('sigma-trilogy',catalog.pricing_breakdown([b for b in ids if b!=removed],config)['offerIds'])
            self.assertEqual(catalog.total_for_items(ids+['book1'],config),1449 if country=='EG' else 58.99)
            self.assertEqual(catalog.total_for_items(ids+catalog.OFFERS['change-starter']['books'],config),1550 if country=='EG' else 64)

    def test_three_book_cart_uses_the_pair_only_when_both_sigma_books_remain(self):
        books = ['book20', 'book21', 'book22', 'book23']
        for removed in books:
            remaining = [book_id for book_id in books if book_id != removed]
            usd = '59.97' if removed in ('book20', 'book21') else '54.98'
            for country, total in [('EG', '1500'), ('SA', usd)]:
                config = localization.pricing_for_country(catalog.PRICES, country)
                result = catalog.pricing_breakdown(remaining, config)
                self.assertEqual(result['total'], Decimal(total))
                self.assertNotIn('sigma-trilogy', result['offerIds'])

    def test_marriage_awareness_pack_requires_all_three_books_and_cannot_stack_overlaps(self):
        from itertools import combinations
        ids = ['book24', 'book23', 'book22']
        self.assertEqual(catalog.OFFERS['marriage-awareness']['books'], ids)
        for country, unit, pack in [('EG', Decimal('500'), Decimal('1000')),
                                    ('SA', Decimal('19.99'), Decimal('39'))]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            self.assertEqual(config['bookPolicies']['book24'],
                             {'price': float(unit), 'bundleEligible': False, 'previewEnabled': False})
            for count in range(4):
                for subset in combinations(ids, count):
                    result = catalog.pricing_breakdown(list(subset), config)
                    self.assertEqual(result['total'], pack if count == 3 else unit * count)
                    self.assertEqual(result['offerIds'], ['marriage-awareness'] if count == 3 else [])
            self.assertEqual(catalog.total_for_items(ids + ['book1'], config),
                             1199 if country == 'EG' else 48.99)
            overlap = catalog.pricing_breakdown(ids + ['book20', 'book21'], config)
            self.assertEqual(overlap['total'], Decimal('1750' if country == 'EG' else '68.99'))
            self.assertEqual(overlap['offerIds'], ['sigma-trilogy'])
            self.assertNotIn('marriage-awareness', overlap['offerIds'])

    def test_advertised_packs_and_related_book_identity(self):
        expected={'change-starter':(['book18','book14','book6'],300,15),
                  'money-and-wealth':(['book4','book17','book9','book8'],350,20),
                  'self-growth':(['book18','book14','book6','book2','book19'],400,23)}
        self.assertEqual(set(catalog.OFFERS),set(expected)|{'sigma-couple','sigma-trilogy','marriage-awareness'})
        for key,(ids,egp,usd) in expected.items():
            self.assertEqual(catalog.OFFERS[key]['books'],ids)
            self.assertNotIn('book20',ids)
            for country,total in [('EG',egp),('SA',usd)]:
                config=localization.pricing_for_country(catalog.PRICES,country)
                self.assertEqual(catalog.total_for_items(ids,config),total)
                self.assertEqual(catalog.total_for_items(list(reversed(ids)),config),total)
                config['offers'][0]['price']=0.01
                self.assertEqual(catalog.total_for_items(ids,config),total)

    def test_overlaps_partial_packs_disjoint_packs_and_sigma(self):
        starter=catalog.OFFERS['change-starter']['books'];growth=catalog.OFFERS['self-growth']['books'];money=catalog.OFFERS['money-and-wealth']['books']
        cases=[(starter,300,15),(growth,400,23),(money,350,20),
               (starter+money,650,35),(growth+money,750,43),
               (growth[:-1],499,24.99),(starter[:-1],299,17.99),
               (['book1','book2','book3'],399,24.99),
               (starter+['book20'],800,34.99),(money+['book20'],850,39.99),
               (growth+['book20'],900,42.99),
               (list(catalog.BOOKS),3599,171.98)]
        for ids,egp,usd in cases:
            for country,expected in [('EG',egp),('SA',usd)]:
                with self.subTest(country=country,ids=ids):
                    actual=catalog.total_for_items(ids,localization.pricing_for_country(catalog.PRICES,country))
                    self.assertEqual(Decimal(str(actual)),Decimal(str(expected)))
        config=localization.pricing_for_country(catalog.PRICES,'EG')
        self.assertEqual(catalog.pricing_breakdown(growth,config)['offerIds'],['self-growth'])
        with self.assertRaises(ValueError):catalog.total_for_items(starter+starter,config)


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
        self.assertIn('data-add-offer="change-starter"',home)
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
