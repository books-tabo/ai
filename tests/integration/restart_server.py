"""Isolated restart fixture: real Handler/store mapping and SQL; synthetic PDF only.

The REST transport is replaced by native PostgreSQL, not an in-memory counter.
The parent integration test validates the destructive-test boundary before launch.
"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import app
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from reportlab.pdfgen import canvas

info=conninfo_to_dict(os.environ['TABO_TEST_DATABASE_URL'])
if info.get('host') not in ('localhost','127.0.0.1','::1') or not info.get('dbname','').startswith('tabo_test') or os.environ.get('TABO_ALLOW_TEST_DATABASE_RESET')!='1':
    raise RuntimeError('Restart fixture requires an isolated test database')

def native_request(path,method='GET',payload=None,params=None,headers=None):
    with psycopg.connect(os.environ['TABO_TEST_DATABASE_URL'],row_factory=dict_row) as connection:
        connection.execute('SET ROLE service_role')
        if path=='/rest/v1/orders':
            assert method=='GET' and set(params)=={'id','select','limit'}
            row=connection.execute('SELECT * FROM public.orders WHERE id=%s',(params['id'][3:],)).fetchone()
            if not row:return []
            row['total']=float(row['total'])
            for key,value in row.items():
                if hasattr(value,'isoformat'):row[key]=value.isoformat()
            return [row]
        if path=='/rest/v1/order_downloads':
            return connection.execute('SELECT book_id,download_count FROM public.order_downloads WHERE order_id=%s',(params['order_id'][3:],)).fetchall()
        assert path=='/rest/v1/rpc/claim_order_download' and method=='POST'
        keys=('p_order_id','p_book_id','p_session_hash','p_device_hash','p_page_count')
        return connection.execute('SELECT public.claim_order_download(%s,%s,%s,%s,%s) AS result',tuple(payload[k] for k in keys)).fetchone()['result']

source=io.BytesIO()
document=canvas.Canvas(source)
document.drawString(50,700,'SYNTHETIC RESTART TEST - NO CUSTOMER CONTENT')
document.save()
app.store.request=native_request
app.store.ready=lambda:True
app.store.original=lambda _:source.getvalue()
server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
print(server.server_port,flush=True)
server.serve_forever()
