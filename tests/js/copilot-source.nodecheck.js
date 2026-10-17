// Click-to-source viewer: pure functions and the DOM it builds. Run with: node --test tests/js/copilot-source.nodecheck.js
// (Jest ignores public/assets, and this file is named so Jest does not collect it.)
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');

// Minimal DOM: enough for el(), appendChild, insertBefore, remove, style, events.
function fakeEl(tag) {
    const e = {
        tag, className: '', textContent: '', children: [], style: {}, attrs: {}, listeners: {}, parent: null,
        appendChild(c) { c.parent = e; e.children.push(c); return c; },
        insertBefore(c, ref) { c.parent = e; const i = e.children.indexOf(ref); e.children.splice(i < 0 ? e.children.length : i, 0, c); return c; },
        remove() { if (e.parent) { e.parent.children = e.parent.children.filter((x) => x !== e); e.parent = null; } },
        setAttribute(k, v) { e.attrs[k] = v; },
        addEventListener(n, f) { e.listeners[n] = f; },
    };
    return e;
}
global.document = { createElement: fakeEl };
const S = require('../../interface/modules/custom_modules/oe-module-copilot/public/assets/copilot-source.js');
global.document.getElementById = () => null;
const dock = require('../../interface/modules/custom_modules/oe-module-copilot/public/assets/copilot.js');

const cite = (o = {}) => Object.assign({
    source_type: 'extracted', source_id: 'Document/42', page_or_section: '1', field_or_chunk_id: 'lab.0',
    quote_or_value: 'HbA1c 7.9 %', bbox: [0.1, 0.2, 0.5, 0.3],
}, o);
const find = (node, cls) => (node.className.split(' ').includes(cls) ? node
    : node.children.map((c) => find(c, cls)).find(Boolean));

test('bbox maps to percentages of the page box', () => {
    const p = S.bboxToPercent([0.1, 0.2, 0.5, 0.3]);
    assert.ok(Math.abs(p.left - 10) < 1e-9 && Math.abs(p.top - 20) < 1e-9);
    assert.ok(Math.abs(p.width - 40) < 1e-9 && Math.abs(p.height - 10) < 1e-9);
});

test('a bbox that cannot be drawn gives null, never a stray box', () => {
    for (const bad of [null, undefined, [], [0, 0, 1], [0.5, 0.5, 0.5, 0.9], [0.6, 0.1, 0.5, 0.9], [-0.1, 0, 0.5, 0.5],
        [0, 0, 1.2, 0.5], ['a', 0, 1, 1], [NaN, 0, 1, 1], 'x']) {
        assert.equal(S.bboxToPercent(bad), null, JSON.stringify(bad));
    }
});

test('document id and page parsing accept only plain positive integers', () => {
    assert.equal(S.documentId('Document/42'), 42);
    for (const bad of ['Document/0', 'Document/4x', 'Patient/42', 'Document/42/../1', '', null, 'Document/1234567890']) {
        assert.equal(S.documentId(bad), null, String(bad));
    }
    assert.equal(S.pageNumber('2'), 2);
    assert.equal(S.pageNumber('p3'), 3);
    for (const bad of ['0', 'x', '1; drop', '', null, '-1']) { assert.equal(S.pageNumber(bad), null, String(bad)); }
});

test('the image URL is built from two integers only', () => {
    assert.equal(S.sourceUrl('/m/source.php', 42, 2), '/m/source.php?document_id=42&page=2');
});

test('viewer shows the page image with the box positioned and the quote as text', () => {
    const v = S.buildViewer('/m/source.php', cite());
    const img = find(v, 'copilot-source-img');
    const box = find(v, 'copilot-source-bbox');
    assert.equal(img.src, '/m/source.php?document_id=42&page=1');
    assert.equal(box.style.left, '10%');
    assert.equal(box.style.top, '20%');
    assert.equal(box.style.width, '40%');
    assert.equal(box.style.height, '10%');
    assert.match(find(v, 'copilot-source-quote').textContent, /HbA1c 7\.9 %/);
});

test('a citation with no drawable bbox still shows the page, without a box', () => {
    const v = S.buildViewer('/m/source.php', cite({ bbox: null }));
    assert.ok(find(v, 'copilot-source-img'));
    assert.equal(find(v, 'copilot-source-bbox'), undefined);
});

test('the full-page fallback box [0,0,1,1] draws no overlay, but a large real box still does', () => {
    assert.equal(S.bboxToPercent([0, 0, 1, 1]), null);
    assert.equal(S.bboxToPercent([0.001, 0.002, 0.999, 0.998]), null);
    const v = S.buildViewer('/m/source.php', cite({ bbox: [0, 0, 1, 1] }));
    assert.ok(find(v, 'copilot-source-img'), 'the page image is still shown');
    assert.equal(find(v, 'copilot-source-bbox'), undefined, 'no overlay for a whole-page box');
    assert.match(find(v, 'copilot-source-quote').textContent, /HbA1c 7\.9 %/);
    // Only the whole page is suppressed: a wide line, or a tall column, is a real region.
    assert.deepEqual(S.bboxToPercent([0, 0.4, 1, 0.45]), { left: 0, top: 40, width: 100, height: 5 });
    assert.ok(S.bboxToPercent([0.05, 0.05, 0.95, 0.95]));
    assert.ok(S.bboxToPercent([0, 0, 1, 0.5]));
});

test('when the page image fails to load, the page and quote text remain', () => {
    const v = S.buildViewer('/m/source.php', cite());
    find(v, 'copilot-source-img').listeners.error();
    assert.equal(find(v, 'copilot-source-page'), undefined);
    assert.ok(find(v, 'copilot-note'));
    assert.match(find(v, 'copilot-source-quote').textContent, /Page 1/);
});

test('a citation that is not a stored document is text only', () => {
    for (const c of [cite({ source_id: 'Observation/9' }), cite({ page_or_section: 'x' })]) {
        const v = S.buildViewer('/m/source.php', c);
        assert.equal(find(v, 'copilot-source-img'), undefined);
        assert.ok(find(v, 'copilot-source-quote'));
    }
    assert.equal(find(S.buildViewer('', cite()), 'copilot-source-img'), undefined);
});

test('server text is only ever set as text, never parsed as markup', () => {
    const v = S.buildViewer('/m/source.php', cite({ quote_or_value: '<img src=x onerror=alert(1)>' }));
    const q = find(v, 'copilot-source-quote');
    assert.ok(q.textContent.includes('<img src=x onerror=alert(1)>'));
    assert.equal(q.children.length, 0);
});

test('briefs and chats group by cited record type, not words in the claim', () => {
    const sources = {
        'Patient/p': { kind: 'Patient' }, 'Condition/c': { kind: 'Problem' },
        'MedicationRequest/m': { kind: 'Medication' }, 'Observation/o': { kind: 'Lab result' },
    };
    const claims = [
        { kind: 'fact', text: 'The patient is 56 years old.', source_ids: ['Patient/p'] },
        { kind: 'fact', text: 'Type 2 diabetes is active.', source_ids: ['Condition/c'] },
        { kind: 'fact', text: 'The patient takes metformin 500 mg twice daily.', source_ids: ['MedicationRequest/m'] },
        { kind: 'fact', text: 'The patient has a recent HbA1c of 7.9 %.', source_ids: ['Observation/o'] },
        { kind: 'flag', text: 'Historical result. Creatinine 44.0 mg/dL (collected 2024-11-11) is high.', rule_based: true, source_ids: ['Observation/old'] },
    ];
    const sections = dock.chartSections(claims, sources);
    assert.deepEqual(sections.map((s) => s[0]), ['Patient', 'Problems', 'Medications', 'Recent labs', 'Historical chart flags']);
    assert.equal(sections[2][1][0].text, claims[2].text);
    assert.equal(sections[3][1][0].text, claims[3].text);
    assert.equal(sections[4][1][0].text, claims[4].text);
});

test('normal citation text shows the page and quote, not raw box coordinates', () => {
    const upload = require('../../interface/modules/custom_modules/oe-module-copilot/public/assets/copilot-upload.js');
    assert.equal(typeof upload, 'object');
    const text = upload.citationText(cite());
    assert.equal(text, 'p1: "HbA1c 7.9 %"');
    assert.equal(text.includes('0.10'), false);
});

test('rendered text decodes entities and leaves clinical wording unchanged', () => {
    assert.equal(dock.displayText('penicillin &' + 'amp; sulfa <' + 'rash>'), 'penicillin & sulfa <rash>');
    assert.equal(dock.displayText('HbA1c is 7.9 %'), 'HbA1c is 7.9 %');
    assert.equal(dock.displayText('<img src=x>'), '<img src=x>');
});

test('the View source button toggles the viewer open and closed', () => {
    const container = fakeEl('div');
    const b = S.button('/m/source.php', cite(), container);
    b.listeners.click();
    assert.equal(container.children.length, 1);
    assert.equal(b.attrs['aria-expanded'], 'true');
    b.listeners.click();
    assert.equal(container.children.length, 0);
    assert.equal(b.attrs['aria-expanded'], 'false');
});
