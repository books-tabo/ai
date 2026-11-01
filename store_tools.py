"""Owner audit links and optional, public browser error configuration."""
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

ORIGIN = 'https://tabobooks.com'
SDK_URL = 'https://browser.sentry-cdn.com/11.10.0/bundle.min.js'
SDK_INTEGRITY = 'sha384-O/8YQaGGVy/n9jIUzcOiaiJVm4t5+JMNiK/zOa0fWvlSld+FquXemEcwJxQ3x/Lr'
ROOT = Path(__file__).resolve().parent


def sentry_config():
    """Reject custom collectors, credentials, ports and non-production deployments."""
    if os.environ.get('TABO_SENTRY_ENABLED') != '1' or os.environ.get('TABO_SITE_ENV', 'production') != 'production':
        return None
    dsn = os.environ.get('SENTRY_DSN', '').strip()
    if not re.fullmatch(r'https://[a-f0-9]{32}@o[0-9]+\.ingest(?:\.(?:us|de))?\.sentry\.io/[0-9]+', dsn):
        return None
    release = hashlib.sha256(b''.join((ROOT / name).read_bytes() for name in
        ('error-monitoring.js', 'checkout-core.js', 'checkout-page.js', 'download-progress.js', 'success.html'))).hexdigest()[:16]
    return {'dsn': dsn, 'ingest': 'https://' + urlsplit(dsn).hostname, 'release': 'tabo-' + release}


def project_url():
    value = os.environ.get('TABO_SENTRY_PROJECT_URL', '').strip()
    # A dashboard link, never an auth token or DSN. Allow only Sentry issue lists.
    if re.fullmatch(r'https://[a-z0-9-]+\.sentry\.io/issues/(?:\?project=[0-9]+)?', value):
        return value
    return 'https://sentry.io/'


def dashboard_tools():
    pages = [('الرئيسية', '/'), ('صفحة كتاب', '/books/book22'), ('العروض', '/offers')]
    return {
        'pagespeed': [{'label': label, **{device: 'https://pagespeed.web.dev/analysis?' + urlencode({
            'url': ORIGIN + path, 'form_factor': device}) for device in ('mobile', 'desktop')}} for label, path in pages],
        'sentry': {'enabled': bool(sentry_config()), 'url': project_url()},
    }
