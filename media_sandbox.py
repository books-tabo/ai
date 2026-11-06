"""Fail-closed, single-job resource boundary for untrusted media on Linux."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

class MediaError(ValueError):
    def __init__(self, message='Media processing rejected', *, code='rejected'):
        self.code = code if code in ('rejected', 'timeout', 'resource') else 'rejected'
        super().__init__(message)

class MediaBusy(MediaError):
    pass

WORKER = Path(__file__).with_name('media_worker.py')
_SLOT = threading.BoundedSemaphore(1)
IMAGE_INPUT = 10_485_760
IMAGE_OUTPUT = 1_000_000
IMAGE_PIXELS = 25_000_000
IMAGE_DIMENSION = 10000
# Limits apply to the child address space, before importing any media library.
POLICIES = {'image': (12, 256 * 1024**2), 'sample': (30, 384 * 1024**2),
            'render': (15, 256 * 1024**2), 'watermark': (120, 384 * 1024**2),
            'email_watermark': (240, 384 * 1024**2)}


def run(operation, source, options, *, input_limit, output_limit, timeout=None):
    if operation not in POLICIES or not isinstance(source, bytes) or not 0 < len(source) <= input_limit:
        raise MediaError('Invalid media input')
    seconds, memory = POLICIES[operation]
    seconds = min(seconds, timeout) if timeout is not None else seconds
    if not math.isfinite(seconds) or seconds <= 0:
        raise MediaError('Media processing timed out')
    metadata = json.dumps(options, ensure_ascii=False).encode()
    if len(metadata) > 16384:
        raise MediaError('Invalid media options')
    if not _SLOT.acquire(blocking=False):
        raise MediaBusy('Media worker busy')
    try:
        with tempfile.TemporaryDirectory(prefix='tabo-media-') as directory:
            root = Path(directory)
            (root / 'input').write_bytes(source)
            (root / 'options.json').write_bytes(metadata)
            cpu = max(1, math.floor(seconds * .75))
            # Background email gets extra elapsed time on shared CPU. Retain
            # the existing watermark CPU ceiling and all memory/output limits.
            if operation == 'email_watermark':
                cpu = min(cpu, 90)
            command = [sys.executable, '-I', str(WORKER), operation, directory,
                       str(memory), str(cpu), str(output_limit)]
            # No payment/storage credentials are inherited; never log child output.
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True,
                                       close_fds=True, env={'LANG': 'C.UTF-8', 'OMP_NUM_THREADS': '1'})
            try:
                try:
                    process.wait(timeout=seconds)
                except subprocess.TimeoutExpired:
                    raise MediaError('Media processing timed out', code='timeout') from None
                if process.returncode != 0:
                    raise MediaError('Media processing rejected', code='resource' if process.returncode < 0 else 'rejected')
                result_path, output_path = root / 'result.json', root / 'output'
                if result_path.stat().st_size > 4096 or not 0 < output_path.stat().st_size <= output_limit:
                    raise MediaError('Invalid media output')
                result = json.loads(result_path.read_bytes())
                if result.get('error') == 'page':
                    raise IndexError('Page outside sample')
                if result.get('ok') is not True:
                    raise MediaError('Media processing rejected')
                return output_path.read_bytes(), result
            finally:
                # Kill the process group even after normal exit: no orphan descendants.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    finally:
        _SLOT.release()
