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
    '10': 59.99,
    '11': 64.99,
    '12': 69.99,
    '13': 74.99,
    '14': 79.99,
    '15': 84.99,
    '16': 89.99,
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
    """Do not disclose visitor IPs to external geolocation services.

    Trusted edge country headers remain the source of country verification.
    Missing edge data fails closed to international pricing.
    """
    return None


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
