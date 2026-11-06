"""PDF stamping implementation, imported only by the resource-limited child."""
import io
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont
import pdf_limits
from media_worker import BoundedBuffer
def make_stamp(name, phone, order_id, width, font_path):
    font = ImageFont.truetype(font_path, 26)
    canvas = Image.new('RGBA',(1800,125),(255,255,255,0))
    draw = ImageDraw.Draw(canvas)
    draw.text((900,10), f'نسخة مرخصة إلى: {name}', fill=(105,105,105,255), font=font, anchor='mt', direction='rtl')
    draw.text((900,52), f'هاتف: {phone}  |  طلب: {order_id}', fill=(105,105,105,255), font=font, anchor='mt', direction='rtl')
    buf=io.BytesIO(); canvas.save(buf,format='PNG'); buf.seek(0)
    return buf

def watermarked(src, order, max_pages, output_limit, font_path):
    pdf_limits.check_source(src)
    reader=PdfReader(io.BytesIO(src) if isinstance(src, bytes) else str(src)); writer=PdfWriter()
    if len(reader.pages)>max_pages:raise ValueError('PDF page limit')
    # The licensed identity bitmap is identical on every page. Reuse it instead
    # of repeating Arabic shaping, PNG encoding and image decoding for each page.
    stamp_image=None
    for page in reader.pages:
        width=float(page.mediabox.width); height=float(page.mediabox.height)
        pdf_limits.check_page_size(width,height)
        if stamp_image is None:
            stamp_image=ImageReader(make_stamp(order['buyer']['name'],order['buyer']['phone'],order['id'],width,font_path))
        overlay=io.BytesIO(); c=Canvas(overlay,pagesize=(width,height),invariant=1)
        c.drawImage(stamp_image, width*.08, max(10,height*.025), width=width*.84, height=40, mask='auto')
        c.save(); overlay.seek(0)
        page.merge_page(PdfReader(overlay).pages[0])
        writer.add_page(page)
        # merge_page creates a combined, uncompressed content stream. Reapply
        # lossless Flate compression so stamping does not inflate the PDF.
        writer.pages[-1].compress_content_streams()
    out=BoundedBuffer(output_limit); writer.write(out); return out.getvalue(),len(reader.pages)
