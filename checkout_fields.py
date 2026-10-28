"""Normalize checkout contact fields identically at the trusted HTTP boundary."""
import re

DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def phone(value):
    if not isinstance(value, str) or len(value) > 80:
        return ''
    value = re.sub(r'[\s().\-\u200e\u200f\u061c]', '', value.translate(DIGITS))
    return '+' + value[2:] if value.startswith('00') else value


def customer(value):
    value = value if isinstance(value, dict) else {}
    clean = {key: value.get(key, '').strip() if isinstance(value.get(key), str) else ''
             for key in ('name', 'email')}
    clean['phone'] = phone(value.get('phone'))
    errors = {}
    if not 2 <= len(clean['name']) <= 100 or any(ord(c) < 32 for c in clean['name']):
        errors['name'] = 'invalid'
    if (len(clean['email']) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', clean['email'])
            or any(ord(c) < 32 for c in clean['email'])):
        errors['email'] = 'invalid'
    if not re.fullmatch(r'\+?[0-9]{8,15}', clean['phone']):
        errors['phone'] = 'invalid'
    return clean, errors
