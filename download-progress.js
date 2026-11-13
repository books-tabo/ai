/* Transfer only through the existing paid, device-bound endpoint. */
(function () {
    'use strict';
    const LIMIT = 100 * 1024 * 1024;
    async function receive(id, order, onProgress) {
        if (!/^book[1-9][0-9]*$/.test(id)) throw new Error('Invalid book');
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 300000);
        try {
            onProgress({phase:'preparing'});
            const response = await fetch('/api/download/' + encodeURIComponent(id), {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({order_id:order}), signal:controller.signal,
                credentials:'same-origin', cache:'no-store'
            });
            if (!response.ok) {
                let message = '';
                try { const data = await response.json(); if (typeof data.error === 'string') message = data.error.slice(0, 500); } catch (_) {}
                const error = new Error(message); error.code = 'http'; throw error;
            }
            if (!(response.headers.get('Content-Type') || '').toLowerCase().startsWith('application/pdf')) throw new Error('Invalid PDF response');
            const encoded = response.headers.get('Content-Encoding');
            const length = Number(response.headers.get('Content-Length'));
            const total = (!encoded || encoded === 'identity') && Number.isSafeInteger(length) && length > 0 ? length : null;
            if (total && total > LIMIT) throw new Error('File exceeds download limit');
            onProgress({phase:'downloading',loaded:0,total});
            let blob;
            if (response.body && typeof response.body.getReader === 'function') {
                const reader = response.body.getReader(), chunks = [];
                let loaded = 0;
                try {
                    while (true) {
                        const part = await reader.read();
                        if (part.done) break;
                        loaded += part.value.byteLength;
                        if (loaded > LIMIT) { await reader.cancel(); throw new Error('File exceeds download limit'); }
                        chunks.push(part.value);
                        onProgress({phase:'downloading',loaded,total});
                    }
                } finally { reader.releaseLock(); }
                if (total && loaded !== total) throw new Error('Incomplete file');
                blob = new Blob(chunks, {type:'application/pdf'});
            } else {
                blob = await response.blob();
            }
            if (!blob.size || blob.size > LIMIT || (total && blob.size !== total) || await blob.slice(0,5).text() !== '%PDF-') throw new Error('Incomplete or invalid file');
            const remainingHeader = response.headers.get('X-Downloads-Remaining');
            const remaining = /^[0-2]$/.test(remainingHeader || '') ? Number(remainingHeader) : null;
            onProgress({phase:'received',loaded:blob.size,total:blob.size});
            return {blob,remaining};
        } catch (error) {
            if (controller.signal.aborted) { const timedOut = new Error(''); timedOut.code = 'timeout'; throw timedOut; }
            throw error;
        } finally { clearTimeout(timeout); }
    }
    function save(blob, id) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = 'TABO-' + id + '.pdf';
        document.body.appendChild(link);
        try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url),60000); }
    }
    window.TaboDownload = {receive,save};
})();
