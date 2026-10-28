"""Read-only live HTTP check. Never creates orders, charges or download claims."""
import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ORIGIN = 'https://tabobooks.com'
LIMIT = 2 * 1024 * 1024


def read(path, payload=None):
    headers = {'Accept': 'application/json', 'Cache-Control': 'no-cache',
               'User-Agent': 'TABO-Quote-Smoke/1'}
    body = None
    if payload is not None:
        headers.update({'Origin': ORIGIN, 'Content-Type': 'application/json'})
        body = json.dumps(payload).encode()
    try:
        with urlopen(Request(ORIGIN + path, data=body, headers=headers), timeout=20) as response:
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT or response.status != 200 or not response.geturl().startswith(ORIGIN + '/'):
                raise RuntimeError('Unexpected live response')
            return raw, response.headers
    except (HTTPError, URLError, TimeoutError) as error:
        status = getattr(error, 'code', 'unavailable')
        raise RuntimeError('Live check failed for ' + path + ' status=' + str(status)) from None


def main():
    health = json.loads(read('/health')[0])
    assert health.get('ok') is True
    config = json.loads(read('/api/config')[0])
    assert config.get('paymentProvider') == 'kashier'
    assert config.get('checkoutEnabled') is True
    pricing = json.loads(read('/api/store-config?country=KW')[0])
    assert pricing['currency'] == 'USD'
    assert pricing['chargeCurrency'] == 'EGP'
    assert pricing['checkoutSupported'] is True
    assert Decimal(str(pricing['prices']['1'])) == Decimal('9.99')
    page = read('/checkout')[0].decode()
    note = page.split('id="chargeCurrencyNote"', 1)[1].split('>', 1)[0]
    assert 'font-size:13px' in note
    raw, headers = read('/api/checkout-quote', {'country': 'KW', 'items': [{'id': 'book1'}]})
    result = json.loads(raw)
    assert result.get('ok') is True
    quote = result['quote']
    assert quote['displayCurrency'] == 'USD' and quote['chargeCurrency'] == 'EGP'
    assert Decimal(quote['displayTotal']) == Decimal('9.99')
    expected = (Decimal(quote['displayTotal']) * Decimal(quote['rate'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    assert Decimal(quote['chargeTotal']) == expected
    assert quote['expiresAt'] - quote['quotedAt'] == 300
    assert isinstance(quote.get('token'), str) and len(quote['token']) > 64
    assert 'no-store' in headers.get('Cache-Control', '')
    # Never output the signed quote token or contact any payment/order endpoint.
    print('Live health/config/13px note and USD-to-EGP quote: PASS')
    print('Public quote:', quote['displayTotal'], 'USD ->', quote['chargeTotal'], 'EGP; rate date:', quote['rateDate'])
    print('No order, payment, buyer-session or download endpoint was called.')


if __name__ == '__main__':
    main()
