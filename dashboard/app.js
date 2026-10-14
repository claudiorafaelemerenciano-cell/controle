/* Secretária IA — painel (JS puro, sem dependências). Use ?mock=1 para dados de exemplo. */
(function () {
  'use strict';

  var MOCK = /[?&]mock=1/.test(location.search);
  var POLL_MS = 5000;
  var TOKEN_KEY = 'secretaria_token';
  var $ = function (id) { return document.getElementById(id); };

  var state = { calls: [], messages: [], selectedId: null, detail: null, tab: 'calls', token: '', authOpen: false };
  var timers = {};

  /* ---------- armazenamento seguro ---------- */
  function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  state.token = lsGet(TOKEN_KEY);

  /* ---------- DOM helpers (sem innerHTML com dados) ---------- */
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function el(tag, props, children) {
    var n = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }
  function svg(paths, size) {
    var s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', size || 18); s.setAttribute('height', size || 18);
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) { var p = document.createElementNS(SVG_NS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  var ICON = {
    inbound: ['M16 2v6h6', 'M22 2l-7 7', 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z'],
    outbound: ['M22 8V2h-6', 'M15 9l7-7', 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z'],
    phone: ['M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z'],
    mail: ['M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M22 6l-10 7L2 6'],
    back: ['M19 12H5', 'M12 19l-7-7 7-7']
  };

  /* ---------- formatação (pt-BR) ---------- */
  var STATUS_LABEL = { ringing: 'Chamando', active: 'Em andamento', completed: 'Concluída', failed: 'Falhou' };
  var DIR_LABEL = { inbound: 'Recebida', outbound: 'Realizada' };
  var rtf = (window.Intl && Intl.RelativeTimeFormat) ? new Intl.RelativeTimeFormat('pt-BR', { numeric: 'always' }) : null;
  var dtf = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  var tf = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  var dfull = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'full', timeStyle: 'medium' });

  function parseDate(s) { var d = s ? new Date(s) : null; return d && !isNaN(d) ? d : null; }
  function relTime(s) {
    var d = parseDate(s); if (!d) return '';
    var sec = Math.round((d - Date.now()) / 1000), a = Math.abs(sec);
    if (a < 45) return 'agora mesmo';
    if (a < 3600) return fmtRel(Math.round(sec / 60), 'minute', 'min');
    if (a < 86400) return fmtRel(Math.round(sec / 3600), 'hour', 'h');
    if (a < 86400 * 7) return fmtRel(Math.round(sec / 86400), 'day', 'd');
    return dtf.format(d);
  }
  function fmtRel(n, unit, abbr) {
    var v = Math.abs(n);
    return (n < 0 ? 'há ' : 'em ') + v + ' ' + abbr;
  }
  function fmtDuration(c) {
    var s = parseDate(c.started_at); if (!s) return '';
    var e = parseDate(c.ended_at) || (c.status === 'active' || c.status === 'ringing' ? new Date() : null);
    if (!e) return '';
    var sec = Math.max(0, Math.round((e - s) / 1000));
    var m = Math.floor(sec / 60), r = sec % 60;
    return m ? m + ' min ' + (r < 10 ? '0' : '') + r + ' s' : r + ' s';
  }
  function fmtPhone(n) {
    if (!n) return 'Desconhecido';
    var m = /^\+55(\d{2})(9?\d{4})(\d{4})$/.exec(String(n));
    return m ? '+55 (' + m[1] + ') ' + m[2] + '-' + m[3] : String(n);
  }
  function peer(c) { return c.direction === 'outbound' ? c.to_number : c.from_number; }

  /* ---------- API ---------- */
  function api(path, opts) {
    if (MOCK) return window.__mockApi(path, opts || {});
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (opts.body) headers['Content-Type'] = 'application/json';
    if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
    return fetch(path, { method: opts.method || 'GET', headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        if (r.status === 401) { askToken(); var e = new Error('Não autorizado'); e.status = 401; throw e; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (j) {
            var msg = j && (typeof j.detail === 'string' ? j.detail : (Array.isArray(j.detail) && j.detail[0] && j.detail[0].msg) || j.message);
            var e = new Error(msg || 'Erro ' + r.status + ' ao falar com o servidor.'); e.status = r.status; throw e;
          });
        }
        return r.json();
      });
  }
  function netFail(err) {
    if (err && err.status === 401) return;
    var b = $('net-banner');
    b.textContent = 'Não foi possível atualizar os dados. Tentando novamente…';
    b.hidden = false;
  }
  function netOk() { $('net-banner').hidden = true; }

  /* ---------- token ---------- */
  function askToken() {
    if (state.authOpen) return;
    state.authOpen = true;
    var d = $('auth-dialog');
    if (d.showModal) d.showModal(); else d.setAttribute('open', '');
    setTimeout(function () { $('f-token').focus(); }, 30);
  }
  $('auth-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var v = $('f-token').value.trim(); if (!v) return;
    state.token = v; lsSet(TOKEN_KEY, v);
    $('auth-dialog').close(); state.authOpen = false; $('f-token').value = '';
    refreshAll();
  });
  $('auth-dialog').addEventListener('cancel', function (e) { e.preventDefault(); });

  /* ---------- toasts ---------- */
  function toast(msg, kind) {
    var t = el('div', { class: 'toast' + (kind === 'error' ? ' toast-error' : ''), text: msg });
    $('toasts').appendChild(t);
    setTimeout(function () { t.remove(); }, 5000);
  }

  /* ---------- status ---------- */
  function loadStatus() {
    return api('/api/status').then(function (s) {
      netOk();
      $('owner').textContent = s.owner_name ? 'Assistente de ' + s.owner_name : 'Assistente pessoal';
      var pill = $('sip-pill');
      pill.className = 'pill ' + (s.sip_registered ? 'pill-on' : 'pill-off');
      $('sip-text').textContent = s.sip_registered ? 'SIP registrado' : 'SIP desconectado';
      var n = Number(s.active_calls) || 0;
      $('active-count').textContent = n;
      $('active-label').textContent = n === 1 ? 'ligação ativa' : 'ligações ativas';
      $('active-pill').className = 'pill ' + (n ? 'pill-live' : 'pill-neutral');
    }).catch(netFail);
  }

  /* ---------- lista de ligações ---------- */
  function callRow(c) {
    var sel = c.id === state.selectedId;
    var meta = [el('span', { class: 'badge badge-' + c.status, text: STATUS_LABEL[c.status] || c.status })];
    var dur = fmtDuration(c);
    if (dur) meta.push(el('span', { text: dur }));
    meta.push(el('span', { text: DIR_LABEL[c.direction] || '' }));
    var btn = el('button', {
      class: 'row', type: 'button', 'data-id': c.id, 'aria-current': sel ? 'true' : null,
      'aria-label': (DIR_LABEL[c.direction] || 'Ligação') + ' ' + fmtPhone(peer(c)) + ', ' + (STATUS_LABEL[c.status] || '') + ', ' + relTime(c.started_at),
      onclick: function () { selectCall(c.id); }
    }, [
      el('span', { class: 'dir ' + (c.direction === 'outbound' ? 'dir-out' : 'dir-in'), title: DIR_LABEL[c.direction] }, [svg(ICON[c.direction] || ICON.phone)]),
      el('span', {}, [
        el('span', { class: 'row-top' }, [
          el('span', { class: 'row-num', text: fmtPhone(peer(c)) }),
          el('time', { class: 'row-time', datetime: c.started_at || '', title: parseDate(c.started_at) ? dfull.format(parseDate(c.started_at)) : '', text: relTime(c.started_at) })
        ]),
        el('span', { class: 'row-meta' }, meta),
        c.summary ? el('p', { class: 'row-sum', text: c.summary }) : null
      ])
    ]);
    return el('li', {}, [btn]);
  }
  function emptyBlock(title, text, icon) {
    return el('div', { class: 'empty' }, [svg(ICON[icon], 32), el('h3', { text: title }), el('p', { text: text })]);
  }
  function renderCalls() {
    var ul = $('call-list'), focusId = document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute('data-id');
    ul.textContent = '';
    if (state.calls === null) { ul.appendChild(el('li', {}, [el('div', { class: 'skeleton' })])); ul.appendChild(el('li', {}, [el('div', { class: 'skeleton' })])); return; }
    if (!state.calls.length) { ul.appendChild(el('li', {}, [emptyBlock('Nenhuma ligação ainda', 'As chamadas recebidas e realizadas pela secretária aparecerão aqui.', 'phone')])); return; }
    state.calls.forEach(function (c) { ul.appendChild(callRow(c)); });
    if (focusId) { var f = ul.querySelector('[data-id="' + CSS.escape(focusId) + '"]'); if (f) f.focus(); }
  }
  function loadCalls() {
    return api('/api/calls?limit=50').then(function (list) {
      netOk(); state.calls = Array.isArray(list) ? list : []; renderCalls();
      // atualiza o detalhe se algo mudou
      if (state.selectedId) {
        var c = state.calls.filter(function (x) { return x.id === state.selectedId; })[0];
        if (c && (!state.detail || state.detail.status !== c.status || c.status === 'active')) loadDetail(true);
      }
    }).catch(netFail);
  }

  /* ---------- detalhe ---------- */
  function renderDetail() {
    var box = $('detail'); box.textContent = '';
    var c = state.detail;
    if (!state.selectedId) {
      box.appendChild(el('div', { class: 'detail-empty' }, [svg(ICON.phone, 32), el('h3', { text: 'Selecione uma ligação' }), el('p', { text: 'Veja o resumo e a transcrição completa da conversa.' })]));
      return;
    }
    if (!c) { box.appendChild(el('div', { class: 'skeleton' })); return; }
    box.appendChild(el('button', { class: 'btn back', type: 'button', onclick: closeDetail }, [svg(ICON.back, 16), 'Voltar']));
    var s = parseDate(c.started_at);
    box.appendChild(el('div', { class: 'd-head' }, [
      el('div', {}, [
        el('h2', { text: fmtPhone(peer(c)) }),
        el('p', { class: 'd-sub', text: (c.direction === 'outbound' ? 'Ligação realizada' : 'Ligação recebida') + (s ? ' em ' + dfull.format(s) : '') })
      ]),
      el('span', { class: 'badge badge-' + c.status, text: STATUS_LABEL[c.status] || c.status })
    ]));
    box.appendChild(el('dl', { class: 'kv' }, [
      el('div', {}, [el('dt', { text: 'De' }), el('dd', { text: fmtPhone(c.from_number) })]),
      el('div', {}, [el('dt', { text: 'Para' }), el('dd', { text: fmtPhone(c.to_number) })]),
      el('div', {}, [el('dt', { text: 'Duração' }), el('dd', { text: fmtDuration(c) || '—' })])
    ]));
    box.appendChild(el('div', { class: 'summary' }, [el('h3', { text: 'Resumo' }), el('p', { text: c.summary || (c.status === 'completed' ? 'Sem resumo disponível.' : 'O resumo será gerado ao final da ligação.') })]));
    box.appendChild(el('h3', { class: 'sec-title', text: 'Transcrição' }));
    var tr = Array.isArray(c.transcript) ? c.transcript : [];
    if (!tr.length) box.appendChild(el('p', { class: 'muted', text: 'Nenhuma fala registrada ainda.' }));
    else {
      var chat = el('div', { class: 'chat', role: 'log', 'aria-label': 'Transcrição da ligação' });
      tr.forEach(function (m) {
        var agent = m.role === 'agent', t = parseDate(m.ts);
        chat.appendChild(el('div', { class: 'msg ' + (agent ? 'msg-agent' : 'msg-caller') }, [
          el('div', { class: 'bubble', text: m.text }),
          el('span', { class: 'msg-meta', text: (agent ? 'Secretária' : 'Interlocutor') + (t ? ' · ' + tf.format(t) : '') })
        ]));
      });
      box.appendChild(chat);
    }
    if (c.status === 'active' || c.status === 'ringing') box.appendChild(el('p', { class: 'live-note' }, [el('span', { class: 'badge badge-active', text: 'Ao vivo' }), 'Atualizando automaticamente…']));
  }
  function loadDetail(silent) {
    var id = state.selectedId; if (!id) return Promise.resolve();
    return api('/api/calls/' + encodeURIComponent(id)).then(function (c) {
      if (id !== state.selectedId) return;
      netOk();
      var box = $('detail'), pane = $('detail-pane'), atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80, top = pane.scrollTop;
      state.detail = c; renderDetail();
      pane.scrollTop = silent ? (atBottom && c.status === 'active' ? pane.scrollHeight : top) : 0;
      void box;
    }).catch(function (e) { netFail(e); if (!silent) { state.detail = null; toast('Não foi possível carregar a ligação.', 'error'); } });
  }
  function selectCall(id) {
    state.selectedId = id; state.detail = null;
    $('panel-calls').classList.add('show-detail');
    switchTab('calls');
    renderCalls(); renderDetail(); loadDetail(false);
    if (window.matchMedia('(max-width: 860px)').matches) window.scrollTo(0, 0);
    var b = document.querySelector('#detail .back'); if (b && window.matchMedia('(max-width: 860px)').matches) b.focus({ preventScroll: true });
  }
  function closeDetail() {
    state.selectedId = null; state.detail = null;
    $('panel-calls').classList.remove('show-detail');
    renderCalls(); renderDetail();
    var f = document.querySelector('#call-list .row'); if (f) f.focus();
  }

  /* ---------- recados ---------- */
  function renderMessages() {
    var ul = $('message-list'); ul.textContent = '';
    var cnt = $('msg-count');
    cnt.hidden = !state.messages.length; cnt.textContent = state.messages.length;
    if (!state.messages.length) { ul.appendChild(el('li', {}, [emptyBlock('Nenhum recado', 'Quando alguém deixar um recado, ele aparece aqui.', 'mail')])); return; }
    state.messages.forEach(function (m) {
      var who = m.name || 'Pessoa não identificada';
      ul.appendChild(el('li', {}, [el('article', { class: 'msg-row' }, [
        el('div', { class: 'who' }, [
          el('strong', { text: who + (m.phone ? ' · ' + fmtPhone(m.phone) : '') }),
          el('time', { class: 'row-time', datetime: m.created_at || '', title: parseDate(m.created_at) ? dfull.format(parseDate(m.created_at)) : '', text: relTime(m.created_at) })
        ]),
        el('p', { class: 'msg-text', text: m.message }),
        m.call_id ? el('div', { class: 'msg-actions' }, [el('button', { class: 'link', type: 'button', 'aria-label': 'Abrir ligação relacionada ao recado de ' + who, onclick: function () { selectCall(m.call_id); } }, ['Abrir ligação relacionada →'])]) : null
      ])]));
    });
  }
  function loadMessages() {
    return api('/api/messages').then(function (l) { netOk(); state.messages = Array.isArray(l) ? l : []; renderMessages(); }).catch(netFail);
  }

  /* ---------- abas ---------- */
  function switchTab(name) {
    state.tab = name;
    ['calls', 'messages'].forEach(function (t) {
      var on = t === name, tab = $('tab-' + t);
      tab.setAttribute('aria-selected', on ? 'true' : 'false'); tab.tabIndex = on ? 0 : -1;
      $('panel-' + t).hidden = !on;
    });
  }
  ['calls', 'messages'].forEach(function (t, i, arr) {
    var tab = $('tab-' + t);
    tab.addEventListener('click', function () { switchTab(t); if (t === 'messages') loadMessages(); });
    tab.addEventListener('keydown', function (e) {
      var j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? 1 : null;
      if (j === null) return; e.preventDefault();
      var n = arr[(j + 2) % 2]; switchTab(n); $('tab-' + n).focus(); if (n === 'messages') loadMessages();
    });
  });

  /* ---------- nova ligação ---------- */
  var dlg = $('call-dialog');
  function setErr(id, inputId, msg) {
    var e = $(id); e.textContent = msg || ''; e.hidden = !msg;
    if (inputId) { if (msg) $(inputId).setAttribute('aria-invalid', 'true'); else $(inputId).removeAttribute('aria-invalid'); }
  }
  function openDialog() {
    $('call-form').reset(); setErr('f-to-err', 'f-to'); setErr('f-purpose-err', 'f-purpose'); setErr('form-err');
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    $('f-to').focus();
  }
  function closeDialog() { if (dlg.close) dlg.close(); else dlg.removeAttribute('open'); $('new-call-btn').focus(); }
  function normalizePhone(v) {
    var s = v.replace(/[\s().-]/g, '');
    if (/^\d{10,11}$/.test(s)) s = '+55' + s;          // DDD + número sem código do país
    else if (/^55\d{10,11}$/.test(s)) s = '+' + s;
    return s;
  }
  $('new-call-btn').addEventListener('click', openDialog);
  $('dlg-close').addEventListener('click', closeDialog);
  $('dlg-cancel').addEventListener('click', closeDialog);
  dlg.addEventListener('click', function (e) { if (e.target === dlg) closeDialog(); });
  $('call-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var to = normalizePhone($('f-to').value.trim()), purpose = $('f-purpose').value.trim(), ok = true;
    setErr('form-err');
    if (!/^\+[1-9]\d{9,14}$/.test(to)) { setErr('f-to-err', 'f-to', 'Informe um número válido no formato +5511987654321.'); ok = false; } else setErr('f-to-err', 'f-to');
    if (purpose.length < 3) { setErr('f-purpose-err', 'f-purpose', 'Descreva o objetivo da ligação.'); ok = false; } else setErr('f-purpose-err', 'f-purpose');
    if (!ok) { ($('f-to').getAttribute('aria-invalid') ? $('f-to') : $('f-purpose')).focus(); return; }
    var btn = $('dlg-submit'); btn.disabled = true; btn.textContent = 'Ligando…';
    api('/api/calls', { method: 'POST', body: { to: to, purpose: purpose } }).then(function (r) {
      closeDialog(); toast('Ligação iniciada para ' + fmtPhone(to) + '.');
      return loadCalls().then(function () { if (r && r.id) selectCall(r.id); });
    }).catch(function (err) {
      if (err.status === 401) return;
      setErr('form-err', null, err.message && err.message !== 'Failed to fetch' ? err.message : 'Não foi possível iniciar a ligação. Verifique sua conexão e tente novamente.');
      toast('Falha ao iniciar a ligação.', 'error');
    }).then(function () { btn.disabled = false; btn.textContent = 'Ligar agora'; });
  });

  /* ---------- polling ---------- */
  function refreshAll() { return Promise.all([loadStatus(), loadCalls(), loadMessages()]); }
  function startPolling() {
    clearInterval(timers.p);
    timers.p = setInterval(function () {
      if (document.hidden || state.authOpen) return;
      loadStatus(); loadCalls(); if (state.tab === 'messages') loadMessages();
    }, POLL_MS);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refreshAll(); });

  state.calls = null; renderCalls(); renderDetail();
  refreshAll(); startPolling();
})();
