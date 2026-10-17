/* Co-Pilot document upload control. Loaded after copilot.js. Every string from the server goes in with textContent. */
function citationText(c) {
    if (!c) { return 'no citation'; }
    // The box stays on the citation for the page highlight. Normal text shows only the page and quote.
    return 'p' + c.page_or_section + ': "' + c.quote_or_value + '"';
}
if (typeof module === 'object' && module.exports) { module.exports = { citationText: citationText }; }

(function () {
    'use strict';
    var root = document.getElementById('copilot-root');
    var panel = document.getElementById('copilot-panel');
    var log = document.getElementById('copilot-log');
    if (!root || !panel || !log || !root.dataset.uploadEndpoint || root.dataset.uploadReady) { return; }
    root.dataset.uploadReady = '1';

    var ERRORS = {
        csrf: 'Your session expired. Reload the page.',
        forbidden: 'You do not have permission to add documents or lab results.',
        no_patient_open: 'Open a patient chart first.',
        bad_doc_type: 'Choose a document type.',
        empty_file: 'Choose a file.',
        too_large: 'That file is too large.',
        unsupported_type: 'Only PDF, PNG or JPEG files are accepted.',
        too_many_pages: 'That document has too many pages.',
        agent_unreachable: 'The assistant is unavailable right now. The chart is unchanged.'
    };

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text !== undefined) { e.textContent = window.CopilotDisplay ? window.CopilotDisplay(text) : text; }
        return e;
    }

    var box = el('form', 'copilot-upload');
    var type = el('select', 'copilot-upload-type');
    [['lab_pdf', 'Lab report'], ['intake_form', 'Intake form']].forEach(function (o) {
        var opt = el('option', null, o[1]);
        opt.value = o[0];
        type.appendChild(opt);
    });
    var file = el('input', 'copilot-upload-file');
    file.type = 'file';
    file.accept = 'application/pdf,image/png,image/jpeg';
    var go = el('button', 'copilot-send', 'Upload');
    go.type = 'submit';
    box.appendChild(type);
    box.appendChild(file);
    box.appendChild(go);
    panel.insertBefore(box, log);

    function note(cls, text) {
        var m = el('div', 'copilot-msg ' + cls, text);
        log.appendChild(m);
        log.scrollTop = log.scrollHeight;
        return m;
    }

    // The citation as text, plus a "View source" toggle when the cited page can be shown. The viewer opens in `wrap`,
    // under the row. The text stays either way, so a page that fails to load still says where the value came from.
    function withSource(row, wrap, citation) {
        row.appendChild(el('span', 'copilot-note', citationText(citation)));
        if (citation && citation.source_type === 'extracted' && window.CopilotSource) {
            row.appendChild(window.CopilotSource.button(root.dataset.sourceEndpoint, citation, wrap));
        }
    }

    function labRow(r) {
        var wrap = el('div', 'copilot-claim-wrap');
        var row = el('p', 'copilot-claim copilot-' + (r.support === 'supported' ? 'fact' : 'unsupported'));
        var flag = r.abnormal_flag && r.abnormal_flag !== 'unknown' && r.abnormal_flag !== 'normal' ? ' (' + r.abnormal_flag + ')' : '';
        row.appendChild(el('span', 'copilot-claim-text',
            r.test_name + ': ' + r.value + ' ' + (r.unit || '') + flag + (r.support === 'supported' ? '' : ' - not verified, not saved')));
        withSource(row, wrap, r.citation);
        wrap.insertBefore(row, wrap.firstChild);
        return wrap;
    }

    function intakeLine(label, f) {
        var wrap = el('div', 'copilot-claim-wrap');
        var row = el('p', 'copilot-claim copilot-pending');
        row.appendChild(el('span', 'copilot-claim-text', label + ': ' + f.value + ' (for review, not saved to chart)'));
        withSource(row, wrap, f.citation);
        wrap.insertBefore(row, wrap.firstChild);
        return wrap;
    }

    function render(data) {
        var d = data.document, out = el('div', 'copilot-msg copilot-answer');
        if (d.status !== 'extracted') {
            out.appendChild(el('p', 'copilot-claim', 'The document was saved, but it could not be read (' + (d.error_code || 'error') + ').'));
        } else if (d.lab) {
            out.appendChild(el('p', 'copilot-claim', 'Extracted ' + d.lab.rows.length + ' lab result(s)' + (data.duplicate ? ' (already uploaded)' : '') + '.'));
            d.lab.rows.forEach(function (r) { out.appendChild(labRow(r)); });
            var c = data.chart || {};
            out.appendChild(el('p', 'copilot-note', c.state === 'ok'
                ? c.written + ' new result(s) added to the chart as unreviewed; ' + c.skipped + ' not saved.'
                : (c.state === 'failed' ? 'The chart could not be updated; nothing was saved to it.' : '')));
        } else if (d.intake) {
            var i = d.intake;
            out.appendChild(el('p', 'copilot-claim', 'Intake form read. Nothing was changed in the chart.'));
            Object.keys(i.demographics || {}).forEach(function (k) { if (i.demographics[k]) { out.appendChild(intakeLine(k, i.demographics[k])); } });
            [['current_medications', 'Medication'], ['allergies', 'Allergy'], ['family_history', 'Family history']].forEach(function (g) {
                (i[g[0]] || []).forEach(function (f) { out.appendChild(intakeLine(g[1], f)); });
            });
            if (i.chief_concern) { out.appendChild(intakeLine('Chief concern', i.chief_concern)); }
        }
        var g = data.guideline;
        if (g && g.status === 'ok') {
            (g.snippets || []).forEach(function (s) {
                var p = el('p', 'copilot-claim copilot-guideline');
                p.appendChild(el('strong', null, s.publisher + ' (' + s.year + '), ' + s.title + ': '));
                p.appendChild(el('span', 'copilot-claim-text', s.text));
                out.appendChild(p);
            });
            if (g.rerank_skipped) { out.appendChild(el('p', 'copilot-note', 'Guideline ranking used the offline fallback.')); }
        }
        log.appendChild(out);
        log.scrollTop = log.scrollHeight;
    }

    box.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (!file.files || !file.files[0]) { note('copilot-error', ERRORS.empty_file); return; }
        var form = new FormData();
        form.append('file', file.files[0]);
        form.append('doc_type', type.value);
        go.disabled = true;
        var status = note('copilot-status', 'Reading document...');
        try {
            var res = await fetch(root.dataset.uploadEndpoint, {
                method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': root.dataset.csrf }, body: form
            });
            var data = await res.json().catch(function () { return {}; });
            if (res.ok && data.document) { render(data); }
            else { note('copilot-error', ERRORS[data.error] || 'The document could not be processed. Nothing was added to the chart.'); }
        } catch (err) {
            note('copilot-error', ERRORS.agent_unreachable);
        }
        status.remove();
        go.disabled = false;
        file.value = '';
    });
})();
