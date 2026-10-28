"""Disposable PayPal Sandbox with real local PostgreSQL and synthetic PDFs only.

Production never imports this module. The existing REST adapter's transport is
replaced with native SQL only inside this explicitly guarded test process. The
production controller, catalog, PayPal module and SQL payment/download RPCs run
unchanged. This tests those layers together, not Supabase's HTTP gateway.
"""
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_HOSTS = {'tabobooks.com', 'www.tabobooks.com', 'tabo-books-preview.onrender.com'}
FORBIDDEN_PREFIXES = ('PAYPAL_LIVE_', 'SUPABASE_', 'KASHIER_', 'PAYMOB_',
                      'AUTOSEO_', 'TABO_META_', 'TABO_GOOGLE_', 'TABO_SNAP_')
ORDER_FIELDS = frozenset('id status items total currency payment_method status_key download_token buyer transaction_id paymob_order_id created_at paid_at country paymob_method_id checkout_session_hash download_session_hash download_device_hash download_session_expires_at download_token_expires_at download_token_used_at payment_mode pricing_quote'.split())
TABLE_FIELDS = {
    'orders': ORDER_FIELDS,
    'book_stats': frozenset('book_id rating_sum review_count download_count page_count file_size_bytes updated_at'.split()),
    'order_downloads': frozenset('order_id book_id download_count updated_at'.split()),
}
PATCH_FIELDS = frozenset('paymob_order_id checkout_session_hash download_session_hash download_device_hash download_session_expires_at download_token download_token_used_at download_token_expires_at'.split())
RPCS = {
    'apply_paypal_event': ('p_order_id', 'p_mode', 'p_transaction_id', 'p_kind', 'p_amount', 'p_provider_order_id', 'p_digest'),
    'claim_order_download': ('p_order_id', 'p_book_id', 'p_session_hash', 'p_device_hash', 'p_page_count'),
    'list_reader_comments': ('p_book_id', 'p_offset'),
}


def validate_environment(env):
    if env.get('TABO_ENV') != 'paypal-sandbox' or env.get('TABO_SITE_ENV') != 'review' or env.get('PAYPAL_MODE') != 'test':
        raise ValueError('This process requires an explicit PayPal Sandbox environment')
    if any(value and (name.startswith(FORBIDDEN_PREFIXES) or name == 'TABO_ADMIN_KEY') for name, value in env.items()):
        raise ValueError('Production/provider credentials are forbidden in this isolated test service')
    site = urlparse(env.get('TABO_PUBLIC_URL', 'https://paypal-sandbox.invalid'))
    if site.scheme != 'https' or not site.hostname or site.hostname in PRODUCTION_HOSTS or site.username or site.password or site.query or site.fragment or site.path not in ('', '/'):
        raise ValueError('The Sandbox requires its own HTTPS hostname')


def validate_dsn(dsn):
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn)
    if (info.get('host') not in ('localhost', '127.0.0.1', '::1') or
            info.get('hostaddr', info.get('host')) not in ('localhost', '127.0.0.1', '::1') or
            not info.get('dbname', '').startswith('tabo_test') or info.get('options')):
        raise ValueError('Only a local disposable test database is allowed')


def wire_json(value):
    def convert(item):
        if isinstance(item, Decimal): return float(item)
        if hasattr(item, 'isoformat'): return item.isoformat()
        raise TypeError('Unsupported test database value')
    return json.loads(json.dumps(value, default=convert))


class NativeStore:
    def __init__(self, dsn):
        validate_dsn(dsn)
        self.dsn = dsn

    def request(self, path, method='GET', payload=None, params=None, headers=None):
        import psycopg
        import supabase_store
        from psycopg import sql
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        params = dict(params or {})
        payload = dict(payload or {})
        try:
            with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as connection:
                connection.execute('SET ROLE service_role')
                if path.startswith('/rest/v1/rpc/'):
                    name = path.removeprefix('/rest/v1/rpc/')
                    keys = RPCS.get(name)
                    if method != 'POST' or not keys or set(payload) != set(keys):
                        raise ValueError('Unsupported test RPC')
                    args = [sql.SQL('%s::numeric') if key == 'p_amount' else sql.SQL('%s') for key in keys]
                    query = sql.SQL('SELECT public.{}({}) AS result').format(sql.Identifier(name), sql.SQL(',').join(args))
                    return wire_json(connection.execute(query, tuple(payload[key] for key in keys)).fetchone()['result'])
                table = path.removeprefix('/rest/v1/')
                fields = TABLE_FIELDS.get(table)
                if not path.startswith('/rest/v1/') or not fields:
                    raise ValueError('Unsupported test table')
                selected = params.pop('select', '*')
                columns = sql.SQL('*')
                if selected != '*':
                    names = selected.split(',')
                    if not names or not set(names).issubset(fields): raise ValueError('Unsupported test fields')
                    columns = sql.SQL(',').join(map(sql.Identifier, names))
                order = params.pop('order', None)
                limit = params.pop('limit', None)
                clauses, arguments = [], []
                for key, value in params.items():
                    if key not in fields or not isinstance(value, str): raise ValueError('Unsupported test filter')
                    if value == 'is.null':
                        clauses.append(sql.SQL('{} IS NULL').format(sql.Identifier(key)))
                    elif value.startswith(('eq.', 'gt.')):
                        operator, operand = value.split('.', 1)
                        clauses.append(sql.SQL('{} {} %s').format(sql.Identifier(key), sql.SQL('=' if operator == 'eq' else '>')))
                        arguments.append(operand)
                    else: raise ValueError('Unsupported test filter')
                where = sql.SQL(' WHERE ') + sql.SQL(' AND ').join(clauses) if clauses else sql.SQL('')
                qualified = sql.Identifier('public', table)
                if method == 'GET':
                    query = sql.SQL('SELECT {} FROM {}').format(columns, qualified) + where
                    if order:
                        parts = order.split('.')
                        if len(parts) != 2 or parts[0] not in fields or parts[1] not in ('asc', 'desc'): raise ValueError('Unsupported test order')
                        query += sql.SQL(' ORDER BY {} {}').format(sql.Identifier(parts[0]), sql.SQL(parts[1].upper()))
                    if limit is not None:
                        count = int(limit)
                        if not 1 <= count <= 1000: raise ValueError('Unsupported test limit')
                        query += sql.SQL(' LIMIT %s'); arguments.append(count)
                    return wire_json(connection.execute(query, arguments).fetchall())
                if table != 'orders' or not payload or not set(payload).issubset(fields):
                    raise ValueError('Unsupported test write')
                def adapted(value): return Jsonb(value) if isinstance(value, (dict, list)) else value
                if method == 'POST':
                    if params or payload.get('payment_method') != 'PayPal' or payload.get('payment_mode') != 'test' or payload.get('currency') != 'USD':
                        raise ValueError('Only synthetic Sandbox PayPal orders are allowed')
                    # Never retain information a visitor typed into this test site.
                    payload['buyer'] = {'name': 'Synthetic Sandbox Buyer', 'phone': '01000000000'}
                    names = list(payload)
                    query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(qualified, sql.SQL(',').join(map(sql.Identifier, names)), sql.SQL(',').join(sql.Placeholder() for _ in names))
                    connection.execute(query, tuple(adapted(payload[key]) for key in names))
                    return None
                if method == 'PATCH' and 'id' in params and set(payload).issubset(PATCH_FIELDS):
                    names = list(payload)
                    updates = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key in names)
                    query = sql.SQL('UPDATE {} SET {}').format(qualified, updates) + where + sql.SQL(' RETURNING *')
                    return wire_json(connection.execute(query, [adapted(payload[key]) for key in names] + arguments).fetchall())
                raise ValueError('Unsupported test write')
        except psycopg.Error as error:
            if error.sqlstate == '23505': raise supabase_store.UniqueConflict(409, '23505') from None
            raise supabase_store.StoreUnavailable(503) from None


def synthetic_pdf():
    from reportlab.pdfgen import canvas
    stream = io.BytesIO()
    document = canvas.Canvas(stream)
    document.drawString(45, 750, 'SYNTHETIC PAYPAL SANDBOX FILE - NOT A PAID BOOK')
    document.drawString(45, 725, 'For checkout, watermark and two-download-limit tests only.')
    document.save()
    return stream.getvalue()


def prepare_database(dsn, pdf):
    import app
    import psycopg
    validate_dsn(dsn)
    with psycopg.connect(dsn) as connection:
        for role in ('anon', 'authenticated', 'service_role'):
            connection.execute('CREATE ROLE ' + role + ' NOLOGIN' + (' BYPASSRLS' if role == 'service_role' else ''))
        for migration in sorted((ROOT / 'supabase/migrations').glob('*.sql')):
            connection.execute(migration.read_text())
        for book in app.BOOKS:
            connection.execute('INSERT INTO public.book_stats(book_id,page_count,file_size_bytes) VALUES(%s,1,%s)', (book, len(pdf)))


def install_synthetic_store(dsn, pdf):
    import app
    native = NativeStore(dsn)
    filenames = {value[1] for value in app.BOOKS.values()}
    def original(filename):
        if filename not in filenames: raise app.store.StoreUnavailable()
        return pdf
    app.store.request = native.request
    app.store.ready = lambda: True
    app.store.original = original
    return native


def add_test_notice():
    notice = '<aside data-paypal-sandbox="true" role="status" style="background:#fff1ad;color:#111;padding:14px;text-align:center">اختبار PayPal فقط — أموال افتراضية وملف تجريبي، وليس كتابًا مدفوعًا. PayPal Sandbox: synthetic files only.</aside>'
    for name in ('index.html', 'book.html', 'info.html', 'offers.html', 'pending.html', 'success.html', 'paypal-return.html'):
        path = ROOT / name
        source = path.read_text()
        if 'data-paypal-sandbox="true"' not in source:
            source = re.sub(r'(<body\b[^>]*>)', lambda match: match.group(1) + notice, source, count=1)
            path.write_text(source)


def main():
    validate_environment(os.environ)
    if os.environ.get('TABO_SANDBOX_CONTAINER') != '1' or os.getuid() == 0:
        raise ValueError('Run this test service only in its non-root disposable container')
    import app
    import psycopg
    directory = Path(tempfile.mkdtemp(prefix='tabo-paypal-sandbox-'))
    process = None
    server = None
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT): signal.signal(signum, lambda *_: stop.set())
    try:
        data = directory / 'database'
        child_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'LANG', 'LC_ALL')}
        subprocess.run(['initdb', '-D', str(data), '--auth-local=trust', '--auth-host=trust', '--no-locale', '-E', 'UTF8'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env)
        process = subprocess.Popen(['postgres', '-D', str(data), '-h', '127.0.0.1', '-k', str(directory), '-c', 'shared_buffers=16MB', '-c', 'max_connections=25', '-c', 'log_min_error_statement=panic'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env)
        deadline = time.monotonic() + 30
        while True:
            if stop.is_set() or process.poll() is not None: raise RuntimeError('Disposable test database stopped')
            try:
                with psycopg.connect('host=127.0.0.1 dbname=postgres user=postgres connect_timeout=1', autocommit=True) as connection:
                    connection.execute('CREATE DATABASE tabo_test_paypal')
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline: raise RuntimeError('Disposable test database did not start') from None
                stop.wait(0.2)
        dsn = 'host=127.0.0.1 dbname=tabo_test_paypal user=postgres'
        pdf = synthetic_pdf()
        prepare_database(dsn, pdf)
        install_synthetic_store(dsn, pdf)
        os.environ.update({'TABO_CHECKOUT_ENABLED': '1', 'TABO_PERSISTENT_STORAGE_READY': '1', 'TABO_ORIGINALS_VERIFIED': '1', 'TABO_SESSION_BINDING_KEY': secrets.token_urlsafe(48), 'TABO_PAYMENT_PROVIDER': 'kashier'})
        os.environ.setdefault('TABO_PUBLIC_URL', 'https://paypal-sandbox.invalid')
        # Increase only the isolated test admission budget; production is unchanged.
        app.POST_LIMITS['/api/orders'] = (30, 600)
        add_test_notice()
        class SandboxHandler(app.Handler):
            def do_POST(self):
                path = urlparse(self.path).path
                if path not in ('/api/orders', '/api/paypal/capture', '/api/paypal/webhook', '/api/download-session') and not re.fullmatch(r'/api/download/book[1-9][0-9]*', path):
                    return self.respond(403, {'ok': False, 'error': 'This isolated service accepts PayPal Sandbox tests only'})
                return super().do_POST()
        server = app.ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), SandboxHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print('PayPal Sandbox ready: disposable local database and synthetic PDFs only', flush=True)
        while not stop.wait(1):
            if process.poll() is not None or not thread.is_alive(): raise RuntimeError('Disposable test service stopped')
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        # No provider responses, credentials, SQL arguments or visitor data in logs.
        print('Isolated Sandbox startup stopped: ' + type(error).__name__, flush=True)
        raise SystemExit(1) from None
