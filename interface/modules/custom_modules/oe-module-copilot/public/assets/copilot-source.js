/* Co-Pilot click-to-source viewer. Loaded before copilot.js and copilot-upload.js. Shows the cited page of a stored
   document with the cited region boxed. Server text goes in with textContent only; the page is an <img> whose URL is
   built from two integers, never from server text. */
(function (root) {
    'use strict';

    // bbox is page-relative [x0, y0, x1, y1], each 0..1, origin top-left. Returns CSS percentages for an absolutely
    // positioned box over the page image, or null when the value cannot be drawn. The agent uses [0, 0, 1, 1] to mean
    // "somewhere on this page" when it could not locate the quote; that claims no position, so it draws no box (the
    // page and quote still show). A box that spans the whole page is never a real line.
    var FULL_PAGE_TOLERANCE = 0.005;

    function bboxToPercent(bbox) {
        if (!Array.isArray(bbox) || bbox.length !== 4) { return null; }
        var n = bbox.map(Number);
        if (n.some(function (v) { return !isFinite(v) || v < 0 || v > 1; })) { return null; }
        if (!(n[0] < n[2]) || !(n[1] < n[3])) { return null; }
        if (n[0] <= FULL_PAGE_TOLERANCE && n[1] <= FULL_PAGE_TOLERANCE &&
            n[2] >= 1 - FULL_PAGE_TOLERANCE && n[3] >= 1 - FULL_PAGE_TOLERANCE) { return null; }
        var pct = function (v) { return Math.round(v * 100000) / 1000; };
        return { left: pct(n[0]), top: pct(n[1]), width: pct(n[2] - n[0]), height: pct(n[3] - n[1]) };
    }

    // "Document/42" -> 42. Anything else (chart records, guidelines) has no page image.
    function documentId(sourceId) {
        var m = /^Document\/([1-9][0-9]{0,8})$/.exec(String(sourceId || ''));
        return m ? parseInt(m[1], 10) : null;
    }

    // Citation page text ("1", "p1") -> 1-based integer, or null.
    function pageNumber(pageText) {
        var m = /^p?([1-9][0-9]{0,2})$/.exec(String(pageText || '').trim());
        return m ? parseInt(m[1], 10) : null;
    }

    function sourceUrl(endpoint, docId, page) {
        return endpoint + (endpoint.indexOf('?') === -1 ? '?' : '&') + 'document_id=' + docId + '&page=' + page;
    }

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text !== undefined) { e.textContent = text; }
        return e;
    }

    // Builds the inline panel for one extracted citation. The text fallback (page and quote) is always present, so a
    // page that cannot be loaded still shows where the claim comes from.
    function buildViewer(endpoint, citation) {
        var docId = documentId(citation.source_id);
        var page = pageNumber(citation.page_or_section);
        var panel = el('div', 'copilot-source-view');
        var text = el('p', 'copilot-source-quote', 'Page ' + citation.page_or_section + ': "' + citation.quote_or_value + '"');
        if (!endpoint || docId === null || page === null) {
            panel.appendChild(text);
            return panel;
        }
        var wrap = el('div', 'copilot-source-page');
        var img = el('img', 'copilot-source-img');
        img.alt = 'Page ' + page + ' of the uploaded document';
        img.loading = 'lazy';
        wrap.appendChild(img);
        var box = bboxToPercent(citation.bbox);
        if (box) {
            var mark = el('div', 'copilot-source-bbox');
            mark.setAttribute('data-testid', 'copilot-source-bbox');
            mark.style.left = box.left + '%';
            mark.style.top = box.top + '%';
            mark.style.width = box.width + '%';
            mark.style.height = box.height + '%';
            wrap.appendChild(mark);
        }
        img.addEventListener('error', function () {
            wrap.remove();
            panel.insertBefore(el('p', 'copilot-note', 'The page image is not available. The cited text is shown instead.'), text);
        });
        img.src = sourceUrl(endpoint, docId, page);
        panel.appendChild(wrap);
        panel.appendChild(text);
        return panel;
    }

    // A "View source" toggle that opens or closes the viewer inside `container`. Only extracted (document) citations
    // get one; callers skip guideline and chart citations.
    function button(endpoint, citation, container) {
        var b = el('button', 'copilot-cite copilot-view-source', 'View source');
        b.type = 'button';
        b.setAttribute('aria-expanded', 'false');
        var open = null;
        b.addEventListener('click', function () {
            if (open) {
                open.remove();
                open = null;
                b.setAttribute('aria-expanded', 'false');
                return;
            }
            open = buildViewer(endpoint, citation);
            container.appendChild(open);
            b.setAttribute('aria-expanded', 'true');
        });
        return b;
    }

    var api = { bboxToPercent: bboxToPercent, documentId: documentId, pageNumber: pageNumber, sourceUrl: sourceUrl,
                buildViewer: buildViewer, button: button };
    if (typeof module === 'object' && module.exports) { module.exports = api; }
    root.CopilotSource = api;
})(typeof window !== 'undefined' ? window : globalThis);
