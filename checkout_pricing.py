"""Signed, short-lived USD display quotes for an actual EGP charge.

Quotes contain no buyer data or payment credentials. Existing orders retain
their persisted charge amount and currency, including historical USD orders.
"""
import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.request import Request, urlopen

QUOTE_SECONDS = 300
RATE_CACHE_SECONDS = 60
RATE_MAX_AGE_SECONDS = 24 * 60 * 60
_rate_lock = threading.Lock()
_rate_cache = None


class RateUnavailable(RuntimeError):
    pass


class InvalidQuote(ValueError):
    pass


def usd_egp_rate():
    """Bounded single-flight lookup; no invented rate or unbounded lock queue."""
    global _rate_cache
    if not _rate_lock.acquire(timeout=0.1):
        raise RateUnavailable('Exchange rate temporarily unavailable')
    try:
        current = time.time()
        if _rate_cache and 0 <= current - _rate_cache[0] < RATE_CACHE_SECONDS:
            return _rate_cache[1], _rate_cache[2]
        try:
            req = Request('https://api.kashier.io/v3/payment/exchange-rate?from=USD&to=EGP',
                          headers={'Accept': 'application/json'})
            with urlopen(req, timeout=8) as response:
                raw = response.read(8193)
            if len(raw) > 8192:
                raise ValueError()
            data = json.loads(raw)
            rate = Decimal(str(data['rates']['EGP']))
            generated = float(data['timestamp']) / 1000
            rate_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
            if (data.get('success') is not True or data.get('base') != 'USD' or
                    not rate.is_finite() or not 0 < rate < 1_000_000 or
                    not 0 <= current - generated <= RATE_MAX_AGE_SECONDS or
                    abs((datetime.now(timezone.utc).date() - rate_date).days) > 1):
                raise ValueError()
        except Exception:
            raise RateUnavailable('Exchange rate temporarily unavailable') from None
        _rate_cache = (current, rate, data['date'])
        return rate, data['date']
    finally:
        _rate_lock.release()


def _sign(encoded, key):
    if len(key) < 32:
        raise RateUnavailable('Quote signing is unavailable')
    return hmac.new(key.encode(), b'tabo-egp-quote-v1:' + encoded.encode(), hashlib.sha256).hexdigest()


def issue(ids, country, display_total, mode, key):
    rate, rate_date = usd_egp_rate()
    charged = (Decimal(str(display_total)) * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    current = int(time.time())
    quote = {'version': 1, 'items': sorted(ids), 'country': country, 'mode': mode,
             'displayCurrency': 'USD', 'displayTotal': str(display_total),
             'chargeCurrency': 'EGP', 'chargeTotal': str(charged),
             'rate': str(rate), 'rateDate': rate_date, 'quotedAt': current,
             'expiresAt': current + QUOTE_SECONDS}
    encoded = base64.urlsafe_b64encode(json.dumps(quote, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
    return {**quote, 'token': encoded + '.' + _sign(encoded, key)}


def verify(token, ids, country, display_total, mode, key):
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError()
        encoded, signature = token.split('.')
        if not hmac.compare_digest(_sign(encoded, key), signature):
            raise ValueError()
        quote = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        current = int(time.time())
        if (quote['version'] != 1 or quote['items'] != sorted(ids) or quote['country'] != country or
                quote['mode'] != mode or quote['displayCurrency'] != 'USD' or
                Decimal(quote['displayTotal']) != Decimal(str(display_total)) or
                quote['chargeCurrency'] != 'EGP' or not quote['quotedAt'] <= current < quote['expiresAt'] or
                quote['expiresAt'] - quote['quotedAt'] != QUOTE_SECONDS):
            raise ValueError()
        rate, charged = Decimal(quote['rate']), Decimal(quote['chargeTotal'])
        if (not rate.is_finite() or not charged.is_finite() or rate <= 0 or charged <= 0 or
                charged != (Decimal(str(display_total)) * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)):
            raise ValueError()
        return quote
    except RateUnavailable:
        raise
    except (ValueError, TypeError, KeyError, InvalidOperation, UnicodeError):
        raise InvalidQuote('Quote expired or does not match the cart') from None
