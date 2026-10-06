"""Country, currency and localized pricing helpers for TABO BOOKS."""
import json
import math
import os
import threading
import time
from urllib.request import Request, urlopen


COUNTRIES = {
    'EG': {'ar': 'مصر', 'en': 'Egypt', 'currency': 'EGP'},
    'SA': {'ar': 'السعودية', 'en': 'Saudi Arabia', 'currency': 'SAR'},
    'AE': {'ar': 'الإمارات', 'en': 'United Arab Emirates', 'currency': 'AED'},
    'KW': {'ar': 'الكويت', 'en': 'Kuwait', 'currency': 'KWD'},
    'QA': {'ar': 'قطر', 'en': 'Qatar', 'currency': 'QAR'},
    'BH': {'ar': 'البحرين', 'en': 'Bahrain', 'currency': 'BHD'},
    'OM': {'ar': 'عُمان', 'en': 'Oman', 'currency': 'OMR'},
    'JO': {'ar': 'الأردن', 'en': 'Jordan', 'currency': 'JOD'},
    'PS': {'ar': 'فلسطين', 'en': 'Palestine', 'currency': 'ILS'},
    'IQ': {'ar': 'العراق', 'en': 'Iraq', 'currency': 'IQD'},
    'LB': {'ar': 'لبنان', 'en': 'Lebanon', 'currency': 'LBP'},
    'SY': {'ar': 'سوريا', 'en': 'Syria', 'currency': 'SYP'},
    'YE': {'ar': 'اليمن', 'en': 'Yemen', 'currency': 'YER'},
    'SD': {'ar': 'السودان', 'en': 'Sudan', 'currency': 'SDG'},
    'SO': {'ar': 'الصومال', 'en': 'Somalia', 'currency': 'SOS'},
    'DJ': {'ar': 'جيبوتي', 'en': 'Djibouti', 'currency': 'DJF'},
    'KM': {'ar': 'جزر القمر', 'en': 'Comoros', 'currency': 'KMF'},
    'LY': {'ar': 'ليبيا', 'en': 'Libya', 'currency': 'LYD'},
    'TN': {'ar': 'تونس', 'en': 'Tunisia', 'currency': 'TND'},
    'DZ': {'ar': 'الجزائر', 'en': 'Algeria', 'currency': 'DZD'},
    'MA': {'ar': 'المغرب', 'en': 'Morocco', 'currency': 'MAD'},
    'MR': {'ar': 'موريتانيا', 'en': 'Mauritania', 'currency': 'MRU'},
}

# Amount of each currency per EGP. Used only if the live feed is unavailable.
FALLBACK_RATES = {
    'EGP': 1.0, 'SAR': 0.0714, 'AED': 0.0699, 'KWD': 0.00584,
    'QAR': 0.0693, 'BHD': 0.00716, 'OMR': 0.00733, 'JOD': 0.0135,
    'ILS': 0.0705, 'IQD': 24.95, 'LBP': 1705.0, 'SYP': 250.0,
    'YER': 4.76, 'SDG': 11.44, 'SOS': 10.88, 'DJF': 3.38,
    'KMF': 8.35, 'LYD': 0.092, 'TND': 0.058, 'DZD': 2.57,
    'MAD': 0.18, 'MRU': 0.75,
}

_FX_LOCK = threading.Lock()
_FX_CACHE = {'loaded_at': 0.0, 'rates': FALLBACK_RATES.copy(), 'live': False}
_FX_TTL = 12 * 60 * 60


def normalize_country(value):
    code = str(value or '').upper().strip()
    return code if code in COUNTRIES else 'EG'


def country_from_headers(headers):
    """Use privacy-friendly country headers/locale. Browser hint remains the fallback."""
    for name in ('CF-IPCountry', 'CloudFront-Viewer-Country', 'X-Country-Code',
                 'X-Vercel-IP-Country'):
        code = str(headers.get(name, '')).upper().strip()
        if code in COUNTRIES:
            return code
    language = str(headers.get('Accept-Language', ''))
    for part in language.split(','):
        locale = part.split(';', 1)[0].strip().replace('_', '-')
        if '-' in locale:
            code = locale.rsplit('-', 1)[-1].upper()
            if code in COUNTRIES:
                return code
    return 'EG'


def _custom_rates():
    raw = os.environ.get('TABO_FX_RATES_JSON', '').strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
        return {str(k).upper(): float(v) for k, v in values.items()
                if float(v) > 0}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def exchange_rates(force=False):
    """Return EGP-based FX rates, refreshed at most twice daily."""
    with _FX_LOCK:
        age = time.time() - _FX_CACHE['loaded_at']
        if not force and _FX_CACHE['loaded_at'] and age < _FX_TTL:
            return _FX_CACHE['rates'].copy(), _FX_CACHE['live']
        rates = FALLBACK_RATES.copy()
        live = False
        try:
            req = Request('https://open.er-api.com/v6/latest/EGP',
                          headers={'User-Agent': 'TABO-Books/1.0'})
            with urlopen(req, timeout=4) as response:
                payload = json.load(response)
            received = payload.get('rates') if isinstance(payload, dict) else None
            if isinstance(received, dict):
                for currency in set(item['currency'] for item in COUNTRIES.values()):
                    value = received.get(currency)
                    if isinstance(value, (int, float)) and value > 0:
                        rates[currency] = float(value)
                live = True
        except Exception:
            pass
        rates.update(_custom_rates())
        _FX_CACHE.update(loaded_at=time.time(), rates=rates, live=live)
        return rates.copy(), live


def _round_up_friendly(value, currency):
    """Create stable, readable local prices without dropping below conversion."""
    if currency == 'EGP':
        return int(value)
    if value < 1:
        step = 0.05
    elif value < 10:
        step = 0.5
    elif value < 100:
        step = 1
    elif value < 1_000:
        step = 5
    elif value < 10_000:
        step = 50
    elif value < 100_000:
        step = 500
    else:
        step = 1_000
    rounded = math.ceil((value - 1e-12) / step) * step
    return round(rounded, 2) if step < 1 else int(rounded)


def pricing_for_country(base_prices, country):
    code = normalize_country(country)
    meta = COUNTRIES[code]
    rates, live = exchange_rates()
    currency = meta['currency']
    multiplier = 1 if code == 'EG' else 2
    rate = rates.get(currency, FALLBACK_RATES[currency])
    prices = {
        str(count): _round_up_friendly(amount * multiplier * rate, currency)
        for count, amount in base_prices.items()
    }
    return {
        'country': code,
        'currency': currency,
        'multiplier': multiplier,
        'prices': prices,
        'rateSource': 'live' if live else 'fallback',
        'countries': [
            {'code': item_code, 'nameAr': item['ar'], 'nameEn': item['en'],
             'currency': item['currency']}
            for item_code, item in COUNTRIES.items()
        ],
    }


def payment_methods():
    methods = {'EGP': int(os.environ.get('PAYMOB_EGP_METHOD_ID', '5932821'))}
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
