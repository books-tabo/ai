"""Country detection and fixed EGP/USD pricing helpers for TABO BOOKS."""
import json
import os


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
    '1': 5.99,
    '2': 9.99,
    '3': 11.99,
    '4': 13.99,
    '5': 16.99,
    '6': 19.99,
    '7': 21.99,
    '8': 24.99,
    '9': 27.99,
}


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
