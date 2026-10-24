import io,threading,unittest
from unittest.mock import patch,Mock
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader
import app,book_previews,pdf_limits

class PDFResourceTests(unittest.TestCase):
    def test_busy_slot_rejects_before_original_read(self):
        entered=threading.Event();release=threading.Event()
        def hold():
            with pdf_limits.work_slot():entered.set();release.wait(5)
        t=threading.Thread(target=hold);t.start();entered.wait(2)
        fetch=Mock()
        try:
            with self.assertRaises(pdf_limits.PDFBusy):book_previews.get_sample('book1',{'book1':('Synthetic','book1.pdf')},fetch)
            fetch.assert_not_called()
        finally:release.set();t.join()

    def test_source_output_and_page_limits(self):
        with patch.object(pdf_limits,'MAX_SOURCE_BYTES',5):
            with self.assertRaises(ValueError):book_previews.make_sample(b'%PDF-oversized','Test')
        with patch.object(pdf_limits,'MAX_OUTPUT_BYTES',4):
            with self.assertRaises(ValueError):pdf_limits.LimitedBuffer().write(b'12345')
        buf=io.BytesIO();c=Canvas(buf);c.drawString(30,30,'Synthetic test PDF');c.showPage();c.save()
        with patch.object(pdf_limits,'MAX_PAGES',0):
            with self.assertRaises(ValueError):app.watermarked(buf.getvalue(),{'buyer':{'name':'Synthetic','phone':'01000000000'},'id':'TABO-0000000000000001'})

    def test_real_synthetic_pdf_is_watermarked_without_changing_page_count(self):
        buf=io.BytesIO();c=Canvas(buf);c.drawString(30,100,'Synthetic test PDF');c.showPage();c.save()
        result,count=app.watermarked(buf.getvalue(),{'buyer':{'name':'مشتري اختباري','phone':'01000000000'},'id':'TABO-0000000000000001'})
        reader=PdfReader(io.BytesIO(result))
        self.assertEqual(count,1);self.assertEqual(len(reader.pages),1)
        self.assertTrue(reader.pages[0]['/Resources'].get('/XObject'))
