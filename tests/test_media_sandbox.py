"""Independent synthetic image/PDF and real OS-limit tests, all offline."""
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from pypdf import PdfReader, PdfWriter
import app
import autoseo
import book_previews
import media_sandbox as sandbox
import media_worker
import pdf_limits


def picture(format='PNG',size=(64,48)):
    with Image.new('RGB',size,'gold') as image:
        output=io.BytesIO()
        image.save(output,format=format)
        return output.getvalue()


def pdf(pages=20,width=200,height=300):
    writer=PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width,height=height)
    result=io.BytesIO();writer.write(result)
    return result.getvalue()


class ImageBoundaryTests(unittest.TestCase):
    def convert(self,raw,**kwargs):
        return sandbox.run('image',raw,{},input_limit=sandbox.IMAGE_INPUT,
                           output_limit=sandbox.IMAGE_OUTPUT,**kwargs)[0]

    def test_supported_formats_are_identified_from_bytes(self):
        for format in ('JPEG','PNG','WEBP'):
            with self.subTest(format=format):
                result=self.convert(picture(format))
                with Image.open(io.BytesIO(result),formats=('WEBP',)) as output:
                    self.assertEqual(output.size,(64,48))
                    self.assertEqual(output.format,'WEBP')

    def test_unneeded_formats_and_invalid_bytes_rejected(self):
        for format in ('TIFF','JPEG2000','BMP','GIF','XBM'):
            if format=='XBM':
                raw=b'#define x_width 8\n#define x_height 8\nstatic char x_bits[] = {0};'
            else:
                raw=picture(format)
            with self.subTest(format=format),self.assertRaises(sandbox.MediaError):
                self.convert(raw)
        for raw in (b'not an image',picture()[:24]):
            with self.assertRaises(sandbox.MediaError):self.convert(raw)

    def test_disallowed_decoder_is_never_called(self):
        from PIL import TiffImagePlugin,Jpeg2KImagePlugin,XbmImagePlugin
        for plugin,raw in ((TiffImagePlugin.TiffImageFile,picture('TIFF')),
                           (Jpeg2KImagePlugin.Jpeg2KImageFile,picture('JPEG2000')),
                           (XbmImagePlugin.XbmImageFile,b'#define x_width 1\n#define x_height 1\n')):
            # The direct child implementation is inspected offline with poison decoders.
            with patch.object(plugin,'_open',side_effect=AssertionError('decoder reached')) as accept:
                with self.assertRaises(Exception) as caught:
                    media_worker.image_payload(raw)
                self.assertNotIsInstance(caught.exception,AssertionError)
                accept.assert_not_called()

    def test_misleading_url_and_declared_mime_cannot_authorize_tiff(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ,{'AUTOSEO_IMAGE_CACHE_DIR':directory}), \
             patch.object(autoseo,'fetch_image',return_value=picture('TIFF')):
            with self.assertRaises(autoseo.InvalidArticle):
                autoseo.download_media('https://example.test/pretends-to-be.jpg',time.monotonic()+10)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_input_output_dimensions_and_deadline(self):
        with self.assertRaises(sandbox.MediaError):
            sandbox.run('image',b'12345',{},input_limit=4,output_limit=100)
        with self.assertRaises(sandbox.MediaError):self.convert(picture(size=(10001,1)))
        with self.assertRaises(sandbox.MediaError):self.convert(picture(size=(5100,5000)))
        with self.assertRaises(sandbox.MediaError):self.convert(picture(),timeout=-1)
        with self.assertRaises(sandbox.MediaError):
            sandbox.run('image',picture(),{},input_limit=sandbox.IMAGE_INPUT,output_limit=4)


class ProcessLimitTests(unittest.TestCase):
    def fault(self,mode,**options):
        return sandbox.run('image',b'test',{'mode':mode,**options},input_limit=10,output_limit=100)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(sandbox,'WORKER',Path(__file__).parent/'fixtures/resource_worker.py'),
                        patch.object(sandbox.tempfile,'tempdir',self.temp.name)):
            patcher.start();self.addCleanup(patcher.stop)

    def assert_clean(self):
        self.assertEqual(list(Path(self.temp.name).glob('tabo-media-*')),[])
        self.assertTrue(sandbox._SLOT.acquire(blocking=False));sandbox._SLOT.release()

    def test_wall_timeout_kills_child_and_cleans_private_files(self):
        pid_path=Path(self.temp.name)/'pid'
        with patch.dict(sandbox.POLICIES,{'image':(.5,64*1024**2)}):
            start=time.monotonic()
            with self.assertRaisesRegex(sandbox.MediaError,'timed out'):
                self.fault('time',pid_path=str(pid_path))
            self.assertLess(time.monotonic()-start,3)
        with self.assertRaises(ProcessLookupError):os.kill(int(pid_path.read_text()),0)
        self.assert_clean()

    def test_actual_address_space_limit_cpu_limit_and_file_limit(self):
        for mode in ('memory','cpu','disk'):
            with self.subTest(mode=mode),patch.dict(sandbox.POLICIES,{'image':(2,64*1024**2)}):
                with self.assertRaises(sandbox.MediaError):self.fault(mode)
                self.assert_clean()

    def test_child_does_not_inherit_service_credentials(self):
        with patch.dict(os.environ,{'KASHIER_SECRET_KEY':'synthetic-fixture-secret'}):
            self.assertEqual(self.fault('environment')[0],b'absent')
        self.assert_clean()

    def test_concurrent_work_is_rejected_without_queueing(self):
        sandbox._SLOT.acquire()
        try:
            with self.assertRaises(sandbox.MediaBusy):self.fault('memory')
        finally:sandbox._SLOT.release()
        self.assert_clean()


class PDFBoundaryTests(unittest.TestCase):
    def test_sample_and_raster_remain_bounded(self):
        payload,count=book_previews.make_sample(pdf(),'Synthetic')
        self.assertEqual(count,2)
        self.assertEqual(len(PdfReader(io.BytesIO(payload)).pages),2)
        result,meta=sandbox.run('render',payload,{'page_number':1},
                               input_limit=pdf_limits.MAX_SOURCE_BYTES,output_limit=2*1024**2)
        with Image.open(io.BytesIO(result),formats=('JPEG',)) as image:
            self.assertLessEqual(image.width,1400)
            self.assertLessEqual(image.height,2000)
        with self.assertRaises(IndexError):
            sandbox.run('render',payload,{'page_number':10},input_limit=pdf_limits.MAX_SOURCE_BYTES,output_limit=2*1024**2)

    def test_invalid_oversized_page_and_pdf_rejected(self):
        for raw in (b'%PDF-invalid',pdf(width=20000)):
            with self.assertRaises(ValueError):book_previews.make_sample(raw,'Synthetic')
        for width,height in ((float('nan'),10),(10,float('inf')),(-1,10),(0,10)):
            with self.assertRaises(ValueError):pdf_limits.check_page_size(width,height)

    def test_watermark_page_count_and_output_limits(self):
        order={'buyer':{'name':'Synthetic','phone':'0000000000'},'id':'LOCAL-FIXTURE'}
        payload,count=app.watermarked(pdf(3),order)
        self.assertEqual(count,3)
        reader=PdfReader(io.BytesIO(payload))
        self.assertTrue(all(page['/Resources'].get('/XObject') for page in reader.pages))
        with patch.object(pdf_limits,'MAX_OUTPUT_BYTES',100):
            with self.assertRaises(ValueError):app.watermarked(pdf(3),order)
        with patch.object(pdf_limits,'MAX_PAGES',2):
            with self.assertRaises(ValueError):app.watermarked(pdf(3),order)

    def test_471_page_synthetic_book_with_arabic_stamp(self):
        # Generated fixture; never opens a stored original or consumes an entitlement.
        order={'buyer':{'name':'قارئ اختباري','phone':'0000000000'},'id':'LOCAL-471-PAGES'}
        payload,count=app.watermarked(pdf(471),order)
        self.assertEqual(count,471)
        reader=PdfReader(io.BytesIO(payload))
        self.assertEqual(len(reader.pages),471)
        self.assertTrue(reader.pages[-1]['/Resources'].get('/XObject'))
