# Media processing remediation — 28 October 2026

Source baseline: the actual live Render deploy `dep-dbgdc8jl550s73b5gej0`, commit
`4b9c7446684169a17de0be6df2374051f760ab66`, read before implementation.
Target: existing service `srv-dat17h942hec73f39n20`, workspace
`tea-dasvvel9fdbs73fc5r10`, branch `feature/tobo-books-render-preview`.

## Dependency lock

Production requirements pin the entire installed runtime dependency closure:
Pillow **13.0.0**, pypdf **6.21.3**, reportlab **5.0.1**, pypdfium2 **5.14.0**,
and reportlab's previously unpinned transitive dependency charset-normalizer
**3.5.2** (the version already installed on production).
No optional pypdf extras were added. Pillow requires Python >=3.11; production's
build log reports Python 3.14.3. Unit CI now covers Python 3.12 and 3.14.3.

Official references reviewed:
- https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html
- https://pypdf.readthedocs.io/en/stable/meta/CHANGELOG.html
- https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.open

## Boundary and behavior

AutoSEO accepts actual JPEG, PNG or WebP bytes only. Both opens explicitly pass
`formats=('JPEG', 'PNG', 'WEBP')`; actual format, frame count, dimensions and
pixel count are checked. Animated inputs and all other format plugins are
excluded. Extensions and Content-Type do not authorize decoding. Public media
remains a resized WebP derivative with embedded metadata removed.

All image decoding, PDF parsing/sample creation, PDFium rendering and watermarking
run in fresh child processes, with Linux resource limits set **before** imports
of image/PDF libraries. Children receive only the particular input and required
options, no service credential environment, no inherited sockets, no network
requests or database adapter. Stamping receives only buyer name/phone and order
ID, preserving the existing stamp; all temporary data stays under a private
TemporaryDirectory and is deleted on success, decoder failure or forced kill.
This is a resource boundary, not a claim of a complete OS security sandbox.

| Operation | Wall time | Address-space limit | Input | Output |
|---|---:|---:|---:|---:|
| AutoSEO image | 12 seconds, also capped by publishing deadline | 256 MiB | 10 MiB | 1,000,000 bytes |
| PDF sample | 30 seconds | 384 MiB | 50 MiB | 100 MiB |
| Preview raster page | 15 seconds | 256 MiB | 50 MiB | 2 MiB |
| PDF watermark | 120 seconds | 384 MiB | 50 MiB | 100 MiB |

CPU time is limited to 75% of the wall budget (integer seconds, minimum one),
file size to the output budget, open descriptors to 32, and core dumps to zero.
The parent kills the child's process group and waits for termination; failures
return generic existing API errors without decoder details or private data.
Only one media child runs per web process; concurrent work is rejected without
an unbounded queue. There is no fallback to processing inside the web process.

Images: <=25 million pixels, <=10,000 pixels per side, derivative <=1800 per
side. PDFs retain the existing 2,000-page limit; page dimensions must be finite,
positive and <=14,400 PDF points. Preview page selection and 1400x2000 raster
bounds remain unchanged. A slow or excessively expensive legitimate file can
now be rejected; limits must be re-tested with synthetic/local authorized fixtures
before any future increase.

## Validation

Offline independent fixtures cover valid JPEG/PNG/WebP, TIFF/JPEG2000/XBM/BMP/GIF
rejection, misleading image URLs, truncated data, pixel/dimension/input/output
limits, real wall timeout and child termination, actual address-space/CPU/file
limits, credential-environment exclusion, concurrency and temporary-file cleanup.
Poison decoder tests ensure excluded decoder methods are never called. Synthetic
PDF fixtures cover samples, raster bounds, bad dimensions, page/output limits,
Arabic watermark and a 471-page book. No hostile fixture is sent to production.

Run:
```
python -m pip install -r requirements-dev.txt
python -m pip check
python -m ruff check --select E9,F63,F7,F82 .
python -m unittest discover -s tests -v
```
Existing Node tracking, pricing, SEO and curated-offer tests also remain required.
PostgreSQL CI uses only its disposable local test database.

No catalog, prices, layout, book text, database content/schema/RLS/storage,
payment configuration, tracking, ad campaigns or two-download allowance changed.
The download claim still happens only after successful stamping and remains the
same atomic RPC; a failed worker does not consume a download attempt.

## Deployment and rollback

Publish this direct descendant of the live commit to the existing service branch;
merge no other PR. Auto-deploy is off: trigger one deployment, wait for its specific
ID to become live, and verify the install log lists the exact locked versions.
Afterwards use public GET requests only. Do not create orders, exercise payment,
fetch protected originals or submit adversarial files to production.

Rollback source is the baseline SHA above, but doing so restores the older media
dependencies and removes these mitigations. Prefer a corrective follow-up commit
that retains the updated lock. No database or storage migration needs reversal.
