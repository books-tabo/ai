"""Country detection and fixed EGP/USD pricing helpers for TABO BOOKS."""
import catalog
import ipaddress
import json
import os
import threading
import time
from pathlib import Path
from bisect import bisect_right
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
    '17': 94.99,
    '18': 99.99,
    '19': 104.99,
}

_GEO_CACHE = {}
_GEO_CACHE_LOCK = threading.Lock()
_GEO_CACHE_TTL = 6 * 60 * 60

# DB-IP Country Lite 2026-10, CC BY 4.0. Only Egyptian ranges are retained.
_EGYPT_RANGES = {4: [], 6: []}
try:
    _range_data = json.loads(Path(__file__).with_name('egypt-ip-ranges.json').read_text())
    for _version, _start, _end in _range_data['ranges']:
        _EGYPT_RANGES[_version].append((_start, _end))
    for _version in _EGYPT_RANGES:
        _EGYPT_RANGES[_version].sort()
except (OSError, ValueError, KeyError, TypeError):
    _EGYPT_RANGES = {4: [], 6: []}
_EGYPT_STARTS = {version: [start for start, end in ranges]
                 for version, ranges in _EGYPT_RANGES.items()}


def normalize_country(value):
    code = str(value or '').upper().strip()
    return code if code in COUNTRIES else 'EG'


def country_from_proxy_headers(headers):
    """Deprecated: presence of Cloudflare-looking headers proves nothing."""
    return None


def country_from_ip(value):
    """Check Egyptian ranges locally; no visitor IP leaves the server."""
    try:
        address = ipaddress.ip_address(str(value).strip())
        if not address.is_global:
            return None
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        ranges = _EGYPT_RANGES.get(address.version, [])
        index = bisect_right(_EGYPT_STARTS.get(address.version, []), int(address)) - 1
        if index >= 0 and int(address) <= ranges[index][1]:
            return 'EG'
    except ValueError:
        pass
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
        'bookPolicies': catalog.public_policies(currency, prices['1']),
        'compareAtPrice': 299 if egypt else 14.99,
        'rateSource': 'fixed',
        'countries': [
            {'code': item_code, 'nameAr': item['ar'], 'nameEn': item['en'],
             'currency': item['currency']}
            for item_code, item in COUNTRIES.items()
        ],
    }


def payment_methods():
    if os.environ.get('PAYMOB_MODE','test')!='test':return {}
    methods={}
    for currency in ('EGP','USD'):
        value=os.environ.get('PAYMOB_TEST_'+currency+'_METHOD_ID','')
        if value.isdecimal() and int(value)>0:methods[currency]=int(value)
    return methods


def payment_method_for(currency):
    return payment_methods().get(str(currency).upper())


def amount_to_minor(amount, currency):
    """Paymob amounts are currently represented in 1/100 currency units."""
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(amount))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
