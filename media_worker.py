"""One-shot media child. Limits are installed before any decoder is imported."""
import json
from pathlib import Path
import resource
import sys


def set_limits(memory, cpu, output):
    for kind, value in ((resource.RLIMIT_AS, memory), (resource.RLIMIT_CPU, cpu),
                        (resource.RLIMIT_FSIZE, output), (resource.RLIMIT_CORE, 0),
                        (resource.RLIMIT_NOFILE, 32)):
        resource.setrlimit(kind, (value, value))


def image_payload(source):
    import io
    import warnings
    from PIL import Image, ImageOps
    from media_sandbox import IMAGE_DIMENSION, IMAGE_OUTPUT, IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = IMAGE_PIXELS
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    allowed = ('JPEG', 'PNG', 'WEBP')
    # formats restricts which plugins may inspect input, before decoding.
    with Image.open(io.BytesIO(source), formats=allowed) as image:
        if image.format not in allowed or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Unsupported image')
        width, height = image.size
        if min(width, height) <= 0 or max(width, height) > IMAGE_DIMENSION or width * height > IMAGE_PIXELS:
            raise ValueError('Image dimensions')
        image.verify()
    with Image.open(io.BytesIO(source), formats=allowed) as image:
        image.load()
        normalized = ImageOps.exif_transpose(image)
        try:
            with normalized.convert('RGB') as converted:
                converted.thumbnail((1800, 1800))
                # Strip embedded metadata from the public derivative.
                converted.info.clear()
                output = BoundedBuffer(IMAGE_OUTPUT)
                converted.save(output, format='WEBP', quality=85, method=4)
                return output.getvalue()
        finally:
            if normalized is not image:
                normalized.close()


# A hard file-size limit also covers native writes; this bounds in-memory writes.
import io
class BoundedBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, value):
        if self.tell() + len(value) > self.limit:
            raise ValueError('Output too large')
        return super().write(value)


def main():
    operation, directory, memory, cpu, output = sys.argv[1:]
    set_limits(int(memory), int(cpu), int(output))
    # -I ignores PYTHONPATH and cwd; only this trusted source directory is added.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    root = Path(directory)
    options = json.loads((root / 'options.json').read_bytes())
    source = (root / 'input').read_bytes()
    try:
        if operation == 'image':
            payload, result = image_payload(source), {'ok': True}
        elif operation == 'sample':
            from book_previews import _make_sample
            payload, count = _make_sample(source, options['title'], options['max_pages'], int(output))
            result = {'ok': True, 'pages': count}
        elif operation == 'render':
            from book_previews import _render_page
            payload = _render_page(source, options['page_number'], int(output))
            result = {'ok': True}
        elif operation == 'watermark':
            from pdf_processing import watermarked
            payload, count = watermarked(source, options['order'], options['max_pages'], int(output), options['font'])
            result = {'ok': True, 'pages': count}
        else:
            raise ValueError('Unknown media operation')
        if not 0 < len(payload) <= int(output):
            raise ValueError('Output too large')
    except IndexError:
        payload, result = b'!', {'error': 'page'}
    (root / 'output').write_bytes(payload)
    (root / 'result.json').write_text(json.dumps(result))


if __name__ == '__main__':
    main()
