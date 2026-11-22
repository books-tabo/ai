"""Trusted-region payment choices: synthetic gateways only, no production calls."""
import copy
import json
from unittest.mock import patch

import app
from support import HTTPCase


class RegionalPaymentTests(HTTPCase):
    def setUp(self):
        super().setUp();self.install()
        for target,name,value in ((app,'checkout_ready',True),(app,'paypal_ready',True)):
            p=patch.object(target,name,return_value=value);p.start();self.addCleanup(p.stop)
        def insert(value):self.db.orders[value['id']]=copy.deepcopy(value)
        p=patch.object(app.store,'insert',side_effect=insert);self.insert=p.start();self.addCleanup(p.stop)
        self.customer={'name':'Synthetic Buyer','email':'buyer@example.invalid','phone':'+96598726788'}

    def config(self,country='SA'):
        with self.opener.open(self.base+'/api/store-config?country='+country) as response:return json.load(response)

    def test_public_config_has_exactly_one_gateway_for_the_trusted_region(self):
        for ip,requested,provider,currency,unit in [('41.32.1.1','KW','kashier','EGP',149.99),
                ('8.8.8.8','EG','paypal','USD',4.99),('8.8.8.8','KW','paypal','USD',4.99)]:
            with patch.object(app.Handler,'client_id',return_value=ip):
                data=self.config(requested)
                self.assertEqual(data['paymentProvider'],provider)
                self.assertEqual(data['allowedPaymentProviders'],[provider])
                self.assertEqual((data['currency'],data['chargeCurrency']),(currency,currency))
                self.assertTrue(data['checkoutEnabled'])
                self.assertEqual(data['paypalAvailable'],provider=='paypal')
                self.assertTrue(all(p['price']==unit for p in data['bookPolicies'].values()))
                with self.opener.open(self.base+'/api/config') as response:
                    self.assertEqual(json.load(response)['paymentProvider'],provider)

    def test_wrong_gateway_and_spoofed_country_are_rejected_before_side_effects(self):
        with patch.object(app,'payment_checkout') as kashier,patch.object(app.paypal_checkout,'create') as paypal, \
             patch.object(app.checkout_pricing,'issue') as quote:
            for ip,wrong in [('8.8.8.8',('kashier','paymob')),('41.32.1.1',('paypal','paymob'))]:
                with patch.object(app.Handler,'client_id',return_value=ip):
                    for provider in wrong:
                        for path in ('/api/orders','/api/checkout-quote'):
                            code,body,_=self.post(path,{'country':'EG' if ip=='8.8.8.8' else 'KW',
                                'paymentProvider':provider,'items':[{'id':'book20'}],'customer':self.customer},
                                {'CF-IPCountry':'EG','X-Country':'EG','X-Forwarded-For':'41.32.1.1'})
                            self.assertEqual((code,body['code']),(403,'PAYMENT_PROVIDER_NOT_ALLOWED'))
            self.insert.assert_not_called();kashier.assert_not_called();paypal.assert_not_called();quote.assert_not_called()

    def test_international_default_is_paypal_at_the_usd_total_without_conversion(self):
        with patch.object(app.Handler,'client_id',return_value='8.8.8.8'), \
             patch.object(app.paypal_checkout,'create',return_value=('https://www.sandbox.paypal.com/checkoutnow?token=PAYPALORDER1234567','PAYPALORDER1234567')) as paypal, \
             patch.object(app,'payment_checkout') as kashier,patch.object(app.checkout_pricing,'issue') as issue, \
             patch.object(app.checkout_pricing,'verify') as verify:
            code,body,_=self.post('/api/checkout-quote',{'country':'EG','items':[{'id':'book20'}]})
            self.assertEqual((code,body['quote']),(200,None))
            code,body,_=self.post('/api/orders',{'country':'EG','items':[{'id':'book20','price':0.01}],
                                               'customer':self.customer,'total':1,'currency':'EGP'})
            self.assertEqual(code,201)
            self.assertEqual((body['order']['total'],body['order']['currency']),(4.99,'USD'))
            saved=paypal.call_args.args[0]
            self.assertEqual(saved['paymentMethod'],'PayPal');self.assertIsNone(saved['pricingQuote'])
            kashier.assert_not_called();issue.assert_not_called();verify.assert_not_called()

    def test_egypt_default_is_kashier_and_saved_international_selection_cannot_change_it(self):
        with patch.object(app.Handler,'client_id',return_value='41.32.1.1'), \
             patch.object(app,'payment_checkout',return_value=('https://payments.kashier.io/session/synthetic','session-synthetic')) as kashier, \
             patch.object(app.paypal_checkout,'create') as paypal:
            code,body,_=self.post('/api/orders',{'country':'KW','items':[{'id':'book20'}],
                'customer':self.customer,'total':1,'currency':'USD'})
            self.assertEqual(code,201)
            self.assertEqual((body['order']['total'],body['order']['currency']),(149.99,'EGP'))
            self.assertEqual(kashier.call_args.args[0]['paymentMethod'],'Kashier')
            paypal.assert_not_called()

    def test_paypal_unavailable_never_falls_back_to_kashier(self):
        with patch.object(app.Handler,'client_id',return_value='8.8.8.8'),patch.object(app,'paypal_ready',return_value=False), \
             patch.object(app,'payment_checkout') as kashier,patch.object(app.paypal_checkout,'create') as paypal:
            config=self.config('EG')
            self.assertEqual(config['paymentProvider'],'paypal')
            self.assertEqual(config['allowedPaymentProviders'],['paypal'])
            self.assertFalse(config['checkoutEnabled']);self.assertFalse(config['paypalAvailable'])
            code,_,_=self.post('/api/orders',{'country':'EG','items':[{'id':'book1'}],'customer':self.customer})
            self.assertEqual(code,503)
            self.insert.assert_not_called();kashier.assert_not_called();paypal.assert_not_called()
