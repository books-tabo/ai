"""Real reader comments; private email fingerprint, server-verified purchase badge."""
import hashlib
import hmac
import os
import re
import unicodedata

MAX_BODY = 2000


def validate(data, books):
    book_id = data.get('book_id')
    email = data.get('email')
    name = data.get('display_name', '')
    body = data.get('body')
    if not isinstance(book_id,str) or book_id not in books or data.get('consent') is not True:
        raise ValueError('Invalid comment')
    if not all(isinstance(value, str) for value in (email, name, body)):
        raise ValueError('Invalid comment')
    email = unicodedata.normalize('NFKC', email.strip()).casefold()
    name = unicodedata.normalize('NFKC', name.strip())
    body = unicodedata.normalize('NFKC', body.strip())
    if (len(email) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email)
            or len(name) > 80 or not 5 <= len(body) <= MAX_BODY
            or any(ord(char) < 32 and char not in '\n\t' for char in name + body)
            or any(ord(char) < 32 for char in email)):
        raise ValueError('Invalid comment')
    return {'book_id': book_id, 'email_hash': fingerprint('email', email),
            'display_name': name or 'قارئ', 'body': body}


def fingerprint(purpose, value):
    secret = os.environ.get('SUPABASE_SECRET_KEY', '')
    if len(secret) < 24:
        raise RuntimeError('Comment storage unavailable')
    return hmac.new(secret.encode(), ('reader-comment:' + purpose + ':' + value).encode(), hashlib.sha256).hexdigest()


def public_result(result):
    """Allowlist fields again at the HTTP boundary, even if an adapter changes."""
    if not isinstance(result, dict) or not isinstance(result.get('comments'), list):
        raise ValueError('Invalid comment response')
    comments = []
    for row in result['comments'][:20]:
        comments.append({key: row[key] for key in ('id', 'display_name', 'body', 'verified_purchase', 'created_at')})
    return {'comments': comments, 'total': max(0, int(result.get('total', 0)))}
