/* Clinical Co-Pilot dock. All model/chart text is inserted with textContent only (never innerHTML). */
(function () {
    'use strict';
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
        if (text !== undefined) { e.textContent = text; }
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

    function chip(sourceId) {
        var c = el('span', 'copilot-cite', sourceId.split('/')[0] + ' ' + sourceId.split('/').pop().slice(0, 8));
        c.title = sourceId;
        c.dataset.source = sourceId;
        return c;
    }

    function renderAnswer(data) {
        var box = el('div', 'copilot-msg copilot-answer');
        (data.claims || []).forEach(function (c) {
            var row = el('p', 'copilot-claim copilot-' + c.kind);
            row.appendChild(el('span', 'copilot-claim-text', c.text));
            (c.source_ids || []).forEach(function (s) { row.appendChild(chip(s)); });
            if (c.rule_based) { row.appendChild(el('span', 'copilot-rule', 'rule check')); }
            box.appendChild(row);
        });
        if (!(data.claims || []).length) {
            box.appendChild(el('p', 'copilot-claim', 'No verified statements could be produced for that question.'));
        }
        if (data.dropped_count) {
            box.appendChild(el('p', 'copilot-note', data.dropped_count + ' unverified statement(s) were withheld.'));
        }
        (data.notes || []).forEach(function (n) { box.appendChild(el('p', 'copilot-note', n)); });
        log.appendChild(box);
        log.scrollTop = log.scrollHeight;
    }

    function renderDegraded(data) {
        var box = el('div', 'copilot-msg copilot-degraded');
        box.appendChild(el('p', 'copilot-claim', data.message || 'The language model is unavailable. Showing chart data only.'));
        (data.records || []).forEach(function (env) {
            var h = el('p', 'copilot-claim', env.tool + (env.empty ? ': none documented' : ': ' + env.records.length + ' record(s)'));
            (env.source_ids || []).forEach(function (s) { h.appendChild(chip(s)); });
            box.appendChild(h);
        });
        log.appendChild(box);
        log.scrollTop = log.scrollHeight;
    }

    function handle(event, data, state) {
        if (event === 'conversation') { conversationId = data.id; }
        else if (event === 'step') { state.status.textContent = 'Checking ' + String(data.tool).replace(/_/g, ' ') + '...'; }
        else if (event === 'answer') { renderAnswer(data); state.done = true; }
        else if (event === 'degraded') { renderDegraded(data); state.done = true; }
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
