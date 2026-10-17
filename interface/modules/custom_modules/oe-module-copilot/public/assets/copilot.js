/* Clinical Co-Pilot dock. All model/chart text is inserted with textContent only (never innerHTML). */
(function () {
    'use strict';

    var SECTION_BY_KIND = { Patient: 'Patient', Problem: 'Problems', Medication: 'Medications',
        Allergy: 'Allergies', 'Lab result': 'Recent labs', Encounter: 'Encounters' };

    // Groups follow the cited record type, not words in the claim. A medication sentence that names the patient
    // stays under Medications. Rule checks are historical chart flags. Uncited claims still render.
    function chartSections(claims, sources) {
        var order = ['Patient', 'Problems', 'Medications', 'Allergies', 'Recent labs', 'Encounters', 'Other chart facts', 'Historical chart flags'];
        var buckets = {};
        order.forEach(function (name) { buckets[name] = []; });
        (claims || []).forEach(function (c) {
            if (c.kind === 'flag' || c.rule_based) { buckets['Historical chart flags'].push(c); return; }
            var kinds = (c.source_ids || []).map(function (id) { return sources && sources[id] && sources[id].kind; }).filter(Boolean);
            var unique = kinds.filter(function (k, i) { return kinds.indexOf(k) === i; });
            var name = unique.length === 1 ? SECTION_BY_KIND[unique[0]] : null;
            buckets[name || 'Other chart facts'].push(c);
        });
        return order.filter(function (name) { return buckets[name].length; }).map(function (name) { return [name, buckets[name]]; });
    }

    // Entities can survive in verified text. Decode only named entities and numeric character references, never tags.
    function displayText(value) {
        return String(value == null ? '' : value).replace(/&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);/g, function (entity) {
            var named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
            var key = entity.slice(1, -1);
            if (named[key]) { return named[key]; }
            var hex = /^&#x([0-9a-fA-F]+);$/.exec(entity);
            var dec = /^&#(\d+);$/.exec(entity);
            var code = hex ? parseInt(hex[1], 16) : parseInt(dec[1], 10);
            return code >= 32 && code !== 127 ? String.fromCodePoint(code) : entity;
        });
    }
    if (typeof module === 'object' && module.exports) { module.exports = { chartSections: chartSections, displayText: displayText }; }
    if (typeof window !== 'undefined') { window.CopilotDisplay = displayText; }

    var root = document.getElementById('copilot-root');
    if (!root || root.dataset.ready) { return; }
    root.dataset.ready = '1';
    var endpoint = root.dataset.endpoint;
    var csrf = root.dataset.csrf;
    var conversationId = null;
    var busy = false;
    var lastPid = null;

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text !== undefined) { e.textContent = displayText(text); }
        return e;
    }

    var toggle = el('button', 'copilot-toggle', 'Co-Pilot');
    toggle.id = 'copilot-toggle';
    toggle.type = 'button';
    var panel = el('section', 'copilot-panel');
    panel.id = 'copilot-panel';
    panel.hidden = true;
    var head = el('header', 'copilot-head');
    head.appendChild(el('strong', null, 'Clinical Co-Pilot'));
    head.appendChild(el('span', 'copilot-sub', 'Read-only chart assistant. Synthetic data.'));
    var log = el('div', 'copilot-log');
    log.id = 'copilot-log';
    log.setAttribute('aria-live', 'polite');
    var form = el('form', 'copilot-form');
    var input = el('input', 'copilot-input');
    input.id = 'copilot-input';
    input.type = 'text';
    input.maxLength = 1000;
    input.placeholder = 'Ask about this patient';
    input.autocomplete = 'off';
    var send = el('button', 'copilot-send', 'Send');
    send.id = 'copilot-send';
    send.type = 'submit';
    form.appendChild(input);
    form.appendChild(send);
    var quick = el('button', 'copilot-quick', 'Pre-visit brief');
    quick.id = 'copilot-brief';
    quick.type = 'button';
    panel.appendChild(head);
    panel.appendChild(quick);
    panel.appendChild(log);
    panel.appendChild(form);
    document.body.appendChild(toggle);
    document.body.appendChild(panel);

    toggle.addEventListener('click', function () {
        panel.hidden = !panel.hidden;
        if (!panel.hidden) { input.focus(); }
    });

    function addMsg(cls, text) {
        var m = el('div', 'copilot-msg ' + cls, text);
        log.appendChild(m);
        log.scrollTop = log.scrollHeight;
        return m;
    }

    // A citation chip is a button. Clicking it opens (or closes) a read-only card for that record, built from the
    // `sources` the agent sent with the answer. All text goes in with textContent.
    function buildCard(src) {
        var card = el('div', 'copilot-source-card');
        card.dataset.source = src.source_id;
        var h = el('p', 'copilot-source-title');
        h.appendChild(el('span', 'copilot-source-kind', src.kind));
        h.appendChild(el('strong', null, src.title));
        card.appendChild(h);
        var dl = el('dl', 'copilot-source-fields');
        (src.fields || []).forEach(function (f) {
            dl.appendChild(el('dt', null, f.label));
            dl.appendChild(el('dd', null, f.value));
        });
        card.appendChild(dl);
        card.appendChild(el('p', 'copilot-source-id', src.source_id));
        return card;
    }

    function chip(sourceId, container, sources, labelOverride) {
        var label = labelOverride || (sourceId.split('/')[0] + ' ' + sourceId.split('/').pop().slice(0, 8));
        var src = (sources || {})[sourceId];
        var c = el('button', 'copilot-cite', label);
        c.type = 'button';
        c.title = sourceId;
        c.dataset.source = sourceId;
        if (!src) {  // no record to show: stays a plain label, never a dead button
            c.disabled = true;
            c.setAttribute('aria-disabled', 'true');
            return c;
        }
        c.setAttribute('aria-expanded', 'false');
        c.addEventListener('click', function () {
            var open = Array.prototype.find.call(container.querySelectorAll('.copilot-source-card'),
                function (n) { return n.dataset.source === sourceId; });
            if (open) {
                open.remove();
                c.setAttribute('aria-expanded', 'false');
                return;
            }
            container.appendChild(buildCard(src));
            c.setAttribute('aria-expanded', 'true');
        });
        return c;
    }

    function indexSources(list) {
        var map = {};
        (list || []).forEach(function (s) { map[s.source_id] = s; });
        return map;
    }

    // Week 2 claims carry whole citations (document or guideline), not ids into `sources`. Each citation becomes a
    // read-only card showing where in the document or guideline the statement comes from.
    function renderCitedClaims(box, claims, heading) {
        if (!(claims || []).length) { return; }
        box.appendChild(el('p', 'copilot-note', heading));
        claims.forEach(function (c) {
            var wrap = el('div', 'copilot-claim-wrap');
            var row = el('p', 'copilot-claim');
            var sources = {};
            row.appendChild(el('span', 'copilot-claim-text', c.text));
            (c.citations || []).forEach(function (ci) {
                var guide = ci.source_type === 'guideline';
                var key = guide ? ci.field_or_chunk_id : ci.source_id + '#' + ci.field_or_chunk_id;
                sources[key] = {
                    source_id: key, kind: guide ? 'guideline' : 'document', title: ci.source_id,
                    fields: [{ label: guide ? 'Section' : 'Page', value: String(ci.page_or_section) },
                             { label: guide ? 'Passage' : 'Quote', value: String(ci.quote_or_value) }]
                };
                row.appendChild(chip(key, wrap, sources, (guide ? 'Guideline ' : 'Document ') + ci.source_id.split('/').pop()));
                // Click-to-source: only extracted document citations have a page image to show.
                if (!guide && ci.source_type === 'extracted' && window.CopilotSource) {
                    row.appendChild(window.CopilotSource.button(root.dataset.sourceEndpoint, ci, wrap));
                }
            });
            wrap.insertBefore(row, wrap.firstChild);
            box.appendChild(wrap);
        });
    }

    function appendClaim(box, claim, sources) {
        var wrap = el('div', 'copilot-claim-wrap');
        var row = el('p', 'copilot-claim copilot-' + claim.kind);
        row.appendChild(el('span', 'copilot-claim-text', claim.text));
        (claim.source_ids || []).forEach(function (s) { row.appendChild(chip(s, wrap, sources)); });
        if (claim.rule_based) { row.appendChild(el('span', 'copilot-rule', 'rule check')); }
        wrap.insertBefore(row, wrap.firstChild);
        box.appendChild(wrap);
    }

    // /v1/w2/chat nests the unchanged Week 1 answer under `chart` and adds two cited groups beside it; /v1/chat sends
    // the Week 1 answer at the top level. Both shapes render here.
    function renderAnswer(data) {
        var box = el('div', 'copilot-msg copilot-answer');
        var w2 = !!data.chart && typeof data.chart === 'object';
        var chart = w2 ? data.chart : data;
        var sources = indexSources(chart.sources);
        chartSections(chart.claims, sources).forEach(function (section) {
            box.appendChild(el('p', 'copilot-note', section[0]));
            section[1].forEach(function (c) { appendClaim(box, c, sources); });
        });
        var docs = w2 ? (data.document_claims || []) : [];
        var guides = w2 ? (data.guideline_claims || []) : [];
        renderCitedClaims(box, docs, 'From uploaded documents');
        renderCitedClaims(box, guides, 'Guideline evidence');
        if (!(chart.claims || []).length && !docs.length && !guides.length) {
            box.appendChild(el('p', 'copilot-claim', 'No verified statements could be produced for that question.'));
        }
        // Withheld statements from every stage are counted: Stage A's own count plus Stage B's.
        var dropped = (data.dropped_count || 0) + (w2 ? (chart.dropped_count || 0) : 0);
        if (dropped) {
            box.appendChild(el('p', 'copilot-note', dropped + ' unverified statement(s) were withheld.'));
        }
        (chart.notes || []).forEach(function (n) { box.appendChild(el('p', 'copilot-note', n)); });
        if (w2) { (data.notes || []).forEach(function (n) { box.appendChild(el('p', 'copilot-note', n)); }); }
        log.appendChild(box);
        log.scrollTop = log.scrollHeight;
    }

    // A refusal, followed by any recorded medications the server fetched and cited. Same claim rows and source chips as an
    // answer; an older response with only a message renders as plain text.
    function renderDeclined(data) {
        var box = el('div', 'copilot-msg copilot-declined');
        box.appendChild(el('p', 'copilot-claim', data.message));
        var sources = indexSources(data.sources);
        (data.claims || []).forEach(function (c) {
            var wrap = el('div', 'copilot-claim-wrap');
            var row = el('p', 'copilot-claim copilot-' + c.kind);
            row.appendChild(el('span', 'copilot-claim-text', c.text));
            (c.source_ids || []).forEach(function (s) { row.appendChild(chip(s, wrap, sources)); });
            wrap.insertBefore(row, wrap.firstChild);
            box.appendChild(wrap);
        });
        (data.notes || []).forEach(function (n) { box.appendChild(el('p', 'copilot-note', n)); });
        log.appendChild(box);
        log.scrollTop = log.scrollHeight;
    }

    function renderDegraded(data) {
        var box = el('div', 'copilot-msg copilot-degraded');
        box.appendChild(el('p', 'copilot-claim', data.message || 'The language model is unavailable. Showing chart data only.'));
        var sources = indexSources(data.sources);
        (data.records || []).forEach(function (env) {
            var wrap = el('div', 'copilot-claim-wrap');
            var h = el('p', 'copilot-claim', env.tool + (env.empty ? ': none documented' : ': ' + env.records.length + ' record(s)'));
            (env.source_ids || []).forEach(function (s) { h.appendChild(chip(s, wrap, sources)); });
            wrap.insertBefore(h, wrap.firstChild);
            box.appendChild(wrap);
        });
        log.appendChild(box);
        log.scrollTop = log.scrollHeight;
    }

    function handle(event, data, state) {
        if (event === 'conversation') { conversationId = data.id; }
        else if (event === 'step') { state.status.textContent = 'Checking ' + String(data.tool).replace(/_/g, ' ') + '...'; }
        else if (event === 'answer') { renderAnswer(data); state.done = true; }
        else if (event === 'degraded') { renderDegraded(data); state.done = true; }
        else if (event === 'declined') { renderDeclined(data); state.done = true; }
        else if (event === 'denied') { addMsg('copilot-denied', data.message); state.done = true; }
        else if (event === 'error') { addMsg('copilot-error', data.message); state.done = true; }
    }

    async function ask(question) {
        if (busy || !question) { return; }
        busy = true;
        send.disabled = true;
        addMsg('copilot-user', question);
        var status = addMsg('copilot-status', 'Working...');
        var state = { status: status, done: false };
        try {
            var res = await fetch(endpoint, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ question: question, conversation_id: conversationId })
            });
            if (res.status === 409) {
                addMsg('copilot-error', 'Open a patient chart first.');
                state.done = true;
            } else if (!res.ok || !res.body) {
                addMsg('copilot-error', 'The assistant is unavailable right now. Chart data in OpenEMR is unaffected.');
                state.done = true;
            } else {
                var reader = res.body.getReader();
                var dec = new TextDecoder();
                var buf = '';
                for (;;) {
                    var r = await reader.read();
                    if (r.done) { break; }
                    buf += dec.decode(r.value, { stream: true });
                    var parts = buf.split('\n\n');
                    buf = parts.pop();
                    parts.forEach(function (block) {
                        var ev = 'message', dat = '';
                        block.split('\n').forEach(function (line) {
                            if (line.indexOf('event: ') === 0) { ev = line.slice(7); }
                            else if (line.indexOf('data: ') === 0) { dat += line.slice(6); }
                        });
                        try { handle(ev, JSON.parse(dat), state); } catch (e) { /* ignore malformed block */ }
                    });
                }
            }
        } catch (e) {
            addMsg('copilot-error', 'The assistant is unavailable right now. Chart data in OpenEMR is unaffected.');
            state.done = true;
        }
        if (!state.done) { addMsg('copilot-error', 'The assistant did not finish. Please try again.'); }
        status.remove();
        busy = false;
        send.disabled = false;
        input.focus();
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var q = input.value.trim();
        input.value = '';
        ask(q);
    });
    quick.addEventListener('click', function () {
        ask('Give me a pre-visit brief for this patient.');
    });

    // A different open patient is a different conversation: reset so no context carries over.
    function currentPid() {
        try {
            var p = window.app_view_model && window.app_view_model.application_data.patient();
            return p ? String(p.pid()) : null;
        } catch (e) { return null; }
    }
    setInterval(function () {
        var pid = currentPid();
        if (pid !== lastPid) {
            if (lastPid !== null || pid === null) { conversationId = null; log.textContent = ''; }
            lastPid = pid;
            if (pid !== null) { addMsg('copilot-note', 'New conversation for the open patient.'); }
        }
    }, 800);
})();
