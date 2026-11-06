"""Isolated SEO review launcher. Do not use this launcher for the live store."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['TABO_SITE_ENV'] = 'review'
os.environ['TABO_META_PIXEL_ID'] = ''
os.environ['TABO_GOOGLE_ADS_ID'] = ''

# A review service has no production credentials, storage or customer data.
if any(value for key, value in os.environ.items() if
       value and (key.startswith('SUPABASE_') or key.startswith('KASHIER_') or key.startswith('PAYMOB_'))):
    raise RuntimeError('Start review without payment or database credentials')

from app import Handler, ThreadingHTTPServer  # noqa: E402

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '3000'))
    print('TABO BOOKS read-only SEO review on port ' + str(port), flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
