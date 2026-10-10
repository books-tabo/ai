"""Country detection and fixed EGP/USD pricing helpers for TABO BOOKS."""
import ipaddress
import json
import os
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


COUNTRIES = {
    'EG': {'ar': 'مصر', 'en': 'Egypt', 'currency': 'EGP'},
    'SA': {'ar': 'السعودية', 'en': 'Saudi Arabia', 'currency': 'USD'},
    'AE': {'ar': 'الإمارات', 'en': 'United Arab Emirates', 'currency': 'USD'},
    'KW': {'ar': 'الكويت', 'en': 'Kuwait', 'currency': 'USD'},
    'QA': {'ar': 'قطر', 'en': 'Qatar', 'currency': 'USD'},
    'BH': {'ar': 'البحرين', 'en': 'Bahrain', 'currency': 'USD'},
    'OM': {'ar': 'عُمان', 'en': 'Oman', 'currency': 'USD'},
    'JO': {'ar': 'الأردن', 'en': 'Jordan', 'currency': 'USD'},
    'PS': {'ar': 'فلسطين', 'en': 'Palestine', 'currency': 'USD'},
    'IQ': {'ar': 'العراق', 'en': 'Iraq', 'currency': 'USD'},
    'LB': {'ar': 'لبنان', 'en': 'Lebanon', 'currency': 'USD'},
    'SY': {'ar': 'سوريا', 'en': 'Syria', 'currency': 'USD'},
    'YE': {'ar': 'اليمن', 'en': 'Yemen', 'currency': 'USD'},
    'SD': {'ar': 'السودان', 'en': 'Sudan', 'currency': 'USD'},
    'SO': {'ar': 'الصومال', 'en': 'Somalia', 'currency': 'USD'},
    'DJ': {'ar': 'جيبوتي', 'en': 'Djibouti', 'currency': 'USD'},
    'KM': {'ar': 'جزر القمر', 'en': 'Comoros', 'currency': 'USD'},
    'LY': {'ar': 'ليبيا', 'en': 'Libya', 'currency': 'USD'},
    'TN': {'ar': 'تونس', 'en': 'Tunisia', 'currency': 'USD'},
    'DZ': {'ar': 'الجزائر', 'en': 'Algeria', 'currency': 'USD'},
    'MA': {'ar': 'المغرب', 'en': 'Morocco', 'currency': 'USD'},
    'MR': {'ar': 'موريتانيا', 'en': 'Mauritania', 'currency': 'USD'},
}

# Fixed international bundle ladder. It preserves the Egyptian bundle discounts
# while keeping one simple price for every supported country outside Egypt.
USD_PRICES = {
    '1': 9.99,
    '2': 17.99,
    '3': 24.99,
    '4': 29.99,
    '5': 34.99,
    '6': 39.99,
    '7': 44.99,
    '8': 49.99,
    '9': 54.99,
}

_GEO_CACHE = {}
_GEO_CACHE_LOCK = threading.Lock()
_GEO_CACHE_TTL = 6 * 60 * 60


def normalize_country(value):
    code = str(value or '').upper().strip()
    return code if code in COUNTRIES else 'EG'


def country_from_proxy_headers(headers):
    """Read the country assigned by Render's Cloudflare edge, not browser locale."""
    if not headers.get('CF-Ray') or not headers.get('CF-Connecting-IP'):
        return None
    code = str(headers.get('CF-IPCountry', '')).upper().strip()
    return code if len(code) == 2 and code.isalpha() else None


def country_from_ip(value):
    """Resolve a public client IP only when the edge did not provide a country."""
    try:
        address = ipaddress.ip_address(str(value or '').strip())
    except ValueError:
        return None
    if not address.is_global:
        return None
    key = address.compressed
    current = time.monotonic()
    with _GEO_CACHE_LOCK:
        cached = _GEO_CACHE.get(key)
        if cached and current - cached[0] < _GEO_CACHE_TTL:
            return cached[1]
    code = None
    try:
        request = Request(
            'https://ipwho.is/' + quote(key, safe='') + '?fields=success,country_code',
            headers={'Accept': 'application/json', 'User-Agent': 'TABO-BOOKS/1.0'},
        )
        with urlopen(request, timeout=2.5) as response:
            raw = response.read(4097)
        if len(raw) <= 4096:
            payload = json.loads(raw)
            candidate = str(payload.get('country_code', '')).upper().strip()
            if payload.get('success') is True and len(candidate) == 2 and candidate.isalpha():
                code = candidate
    except Exception:
        code = None
    with _GEO_CACHE_LOCK:
        if len(_GEO_CACHE) >= 10000:
            _GEO_CACHE.clear()
        _GEO_CACHE[key] = (current, code)
    return code


def pricing_country(requested, detected):
    """Egypt pricing is allowed only when the server detects an Egyptian IP."""
    selected = normalize_country(requested)
    detected_code = str(detected or '').upper().strip()
    if selected != 'EG' or detected_code == 'EG':
        return selected
    if detected_code in COUNTRIES and detected_code != 'EG':
        return detected_code
    return 'SA'


def pricing_for_country(base_prices, country):
    code = normalize_country(country)
    meta = COUNTRIES[code]
    egypt = code == 'EG'
    currency = 'EGP' if egypt else 'USD'
    prices = ({str(count): amount for count, amount in base_prices.items()}
              if egypt else USD_PRICES.copy())
    return {
        'country': code,
        'currency': currency,
        'multiplier': 1 if egypt else 2,
        'prices': prices,
        'compareAtPrice': 299 if egypt else 14.99,
        'rateSource': 'fixed',
        'countries': [
            {'code': item_code, 'nameAr': item['ar'], 'nameEn': item['en'],
             'currency': item['currency']}
            for item_code, item in COUNTRIES.items()
        ],
    }


def payment_methods():
    methods = {'EGP': int(os.environ.get('PAYMOB_EGP_METHOD_ID', '5932821'))}
    usd_method = os.environ.get('PAYMOB_USD_METHOD_ID', '').strip()
    if usd_method:
        try:
            methods['USD'] = int(usd_method)
        except ValueError:
            pass
    raw = os.environ.get('PAYMOB_PAYMENT_METHODS_JSON', '').strip()
    if raw:
        try:
            methods.update({str(k).upper(): int(v) for k, v in json.loads(raw).items()})
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return methods


def payment_method_for(currency):
    return payment_methods().get(str(currency).upper())


def amount_to_minor(amount, currency):
    """Paymob amounts are currently represented in 1/100 currency units."""
    return int(round(float(amount) * 100))
