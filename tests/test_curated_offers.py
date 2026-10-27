"""Advertised totals, mixed carts and crawlable public offer pages."""
import unittest
from decimal import Decimal

import catalog
import localization
import seo
from support import HTTPCase
from urllib.request import Request
from urllib.error import HTTPError


class CuratedPricingTests(unittest.TestCase):
    def test_sigma_pair_is_an_exact_usd_exception_to_independent_prices(self):
        cases=[(['book20'],500,15.9),(['book21'],500,15.99),
               (['book20','book21'],1000,20),
               (['book20','book21','book1'],1199,29.99),
               (['book21','book1','book2'],799,33.98),
               (['book20','book21','book18','book14','book6'],1300,35)]
        for ids,egp,usd in cases:
            for country,expected in [('EG',egp),('SA',usd)]:
                config=localization.pricing_for_country(catalog.PRICES,country)
                self.assertEqual(catalog.total_for_items(ids,config),expected)
                self.assertEqual(catalog.total_for_items(ids[::-1],config),expected)
        usd=localization.pricing_for_country(catalog.PRICES,'SA')
        self.assertEqual(catalog.pricing_breakdown(['book20','book21'],usd)['offerSavings'],Decimal('11.89'))
        self.assertNotIn('sigma-couple',[o['id'] for o in catalog.public_offers('EGP')])
        for bid,price in [('book20',15.9),('book21',15.99)]:
            self.assertEqual(usd['bookPolicies'][bid],{'price':price,'bundleEligible':False,'previewEnabled':False})

    def test_advertised_packs_and_related_book_identity(self):
        expected={'change-starter':(['book18','book14','book6'],300,15),
                  'money-and-wealth':(['book4','book17','book9','book8'],350,20),
                  'self-growth':(['book18','book14','book6','book2','book19'],400,23)}
        self.assertEqual(set(catalog.OFFERS),set(expected)|{'sigma-couple'})
        for key,(ids,egp,usd) in expected.items():
            self.assertEqual(catalog.OFFERS[key]['books'],ids)
            self.assertNotIn('book20',ids)
            for country,total in [('EG',egp),('KW',usd)]:
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
               (starter+['book20'],800,30.9),(money+['book20'],850,35.9),
               (growth+['book20'],900,38.9),
               (list(catalog.BOOKS),2849,122.99)]
        for ids,egp,usd in cases:
            for country,expected in [('EG',egp),('KW',usd)]:
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
        self.assertEqual(len(seo.public_paths()),41)
        self.assertIn('/offers',seo.public_paths())
        home=self.get('/')[2]
        self.assertIn('data-add-offer="change-starter"',home)
        self.assertIn('height:auto',home)
        self.assertIn('id="cartOfferSummary"',home)
