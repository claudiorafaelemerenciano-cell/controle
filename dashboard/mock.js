/* Dados fictícios (pt-BR) para pré-visualizar o painel sem backend: abra /?mock=1 */
(function () {
  var now = Date.now(), min = 60000;
  function ago(m) { return new Date(now - m * min).toISOString(); }
  var calls = [
    { id: 'c1', direction: 'inbound', from_number: '+5511987654321', to_number: '+551133334444', started_at: ago(1.5), ended_at: null, status: 'active', summary: null },
    { id: 'c2', direction: 'inbound', from_number: '+5521998877665', to_number: '+551133334444', started_at: ago(32), ended_at: ago(29), status: 'completed', summary: 'Mariana Souza quer remarcar a reunião de quinta para sexta às 10h e pediu retorno ainda hoje.' },
    { id: 'c3', direction: 'outbound', from_number: '+551133334444', to_number: '+5531991234567', started_at: ago(95), ended_at: ago(93), status: 'completed', summary: 'Consulta de amanhã às 15h confirmada com o Sr. Carlos Almeida. Ele pediu para chegar 10 minutos antes.' },
    { id: 'c4', direction: 'outbound', from_number: '+551133334444', to_number: '+5541988112233', started_at: ago(200), ended_at: ago(200), status: 'failed', summary: 'Não atendeu após 5 toques.' },
    { id: 'c5', direction: 'inbound', from_number: '+5585997001122', to_number: '+551133334444', started_at: ago(60 * 26), ended_at: ago(60 * 26 - 4), status: 'completed', summary: 'Ricardo Tavares, da Gráfica Nordeste, deixou recado sobre o orçamento de 500 cartões.' },
    { id: 'c6', direction: 'inbound', from_number: '+5511976543210', to_number: '+551133334444', started_at: ago(60 * 24 * 3), ended_at: ago(60 * 24 * 3 - 1), status: 'completed', summary: 'Oferta de plano de internet. Recusada educadamente.' }
  ];
  var transcripts = {
    c1: [
      ['agent', 'Olá, você ligou para o escritório do Rafael. Sou a secretária virtual. Como posso ajudar?', 0],
      ['caller', 'Oi, bom dia! Aqui é a Fernanda, da Construtora Horizonte. O Rafael está?', 0.3],
      ['agent', 'No momento ele não pode atender, Fernanda. Posso anotar um recado ou agendar um retorno?', 0.6],
      ['caller', 'Pode anotar sim, é sobre a vistoria do apartamento 42.', 1.0]
    ],
    c2: [
      ['agent', 'Olá, você ligou para o escritório do Rafael. Como posso ajudar?', 0],
      ['caller', 'Oi, é a Mariana Souza. Preciso remarcar a reunião de quinta.', 0.2],
      ['agent', 'Claro, Mariana. Para qual dia e horário você prefere?', 0.5],
      ['caller', 'Sexta, às 10h, se possível. E peça pra ele me ligar ainda hoje, por favor.', 1.1],
      ['agent', 'Anotado! Vou passar o recado ao Rafael. Obrigada pela ligação e até logo.', 1.6]
    ],
    c3: [
      ['agent', 'Bom dia, Sr. Carlos! Falo em nome do Dr. Rafael para confirmar sua consulta de amanhã às 15h.', 0],
      ['caller', 'Bom dia! Confirmo sim, estarei lá.', 0.3],
      ['agent', 'Perfeito. Recomendamos chegar com 10 minutos de antecedência. Tenha um ótimo dia!', 0.7]
    ]
  };
  var messages = [
    { id: 'm1', call_id: 'c2', name: 'Mariana Souza', phone: '+5521998877665', message: 'Quer remarcar a reunião de quinta para sexta às 10h. Pede retorno ainda hoje.', created_at: ago(29) },
    { id: 'm2', call_id: 'c5', name: 'Ricardo Tavares', phone: '+5585997001122', message: 'Gráfica Nordeste: o orçamento dos 500 cartões de visita está pronto. Prazo de entrega de 3 dias úteis.', created_at: ago(60 * 26 - 4) },
    { id: 'm3', call_id: 'c6', name: null, phone: null, message: 'Ligou sobre uma proposta de internet, não deixou contato.', created_at: ago(60 * 24 * 3 - 1) }
  ];
  var extra = ['O Rafael tem acesso ao sistema até as 18h, posso informar a ele?', 'Combinado, vou registrar tudo.'];
  var tick = 0;

  function detail(id) {
    var c = calls.filter(function (x) { return x.id === id; })[0];
    if (!c) return null;
    var base = new Date(c.started_at).getTime();
    var tr = (transcripts[id] || []).map(function (t) { return { role: t[0], text: t[1], ts: new Date(base + t[2] * min).toISOString() }; });
    if (id === 'c1') for (var i = 0; i < Math.min(tick, extra.length); i++) tr.push({ role: i % 2 ? 'caller' : 'agent', text: extra[i], ts: new Date().toISOString() });
    return Object.assign({}, c, { transcript: tr });
  }
  function delay(v) { return new Promise(function (r) { setTimeout(function () { r(JSON.parse(JSON.stringify(v))); }, 120); }); }

  window.__mockApi = function (path, opts) {
    tick++;
    if (path.indexOf('/api/status') === 0) return delay({ sip_registered: true, active_calls: 1, owner_name: 'Rafael Almeida' });
    if (path.indexOf('/api/messages') === 0) return delay(messages);
    if (path.indexOf('/api/calls') === 0) {
      if (opts.method === 'POST') {
        var id = 'n' + Date.now();
        calls.unshift({ id: id, direction: 'outbound', from_number: '+551133334444', to_number: opts.body.to, started_at: new Date().toISOString(), ended_at: null, status: 'ringing', summary: null });
        return delay({ id: id, status: 'ringing' });
      }
      var m = /^\/api\/calls\/([^?]+)/.exec(path);
      if (m) { var d = detail(decodeURIComponent(m[1])); return d ? delay(d) : Promise.reject(Object.assign(new Error('Ligação não encontrada'), { status: 404 })); }
      return delay(calls);
    }
    return Promise.reject(new Error('Rota desconhecida'));
  };
})();
