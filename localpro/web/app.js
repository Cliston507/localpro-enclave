/* LocalPro Enclave: simulated Alexa+ client.
 *
 * This page is a real MCP client. It speaks JSON-RPC 2.0 over Streamable HTTP
 * (initialize, tools/list, tools/call) to the LocalPro Enclave server. The
 * dialogue manager below is a small scripted agent standing in for Alexa+;
 * every fact it says comes from a tool result.
 */
(() => {
  'use strict';

  // Served by the MCP server itself: relative "mcp". In the hosted preview,
  // __PORT_8000__ is rewritten to the proxied backend path.
  const MCP_URL = new URL('__PORT_8000__'.startsWith('__') ? 'mcp' : '__PORT_8000__/mcp', location.href).href;
  const PROTOCOL = '2025-11-25';

  const $ = (sel) => document.querySelector(sel);
  const els = {
    status: $('#status .dot'), statusText: $('#status-text'), endpoint: $('#endpoint'),
    transcript: $('#transcript'), composer: $('#composer'), say: $('#say'), send: $('#composer .primary'),
    mic: $('#mic'), voice: $('#voice-toggle'), reset: $('#reset'), ring: $('#ring'), chips: $('#chips'),
    log: $('#log'), techEmpty: $('#tech-empty'), techBody: $('#tech-body'), woLabel: $('#wo-label'),
    woId: $('#wo-id'), woPro: $('#wo-pro'), woWindow: $('#wo-window'), woToken: $('#wo-token'),
    verdicts: $('#verdicts'),
  };

  // ---------------- MCP client ----------------

  const mcp = { sessionId: null, protocol: null, nextId: 1, ready: null };

  function parseSse(text, id) {
    for (const block of text.split(/\r?\n\r?\n/)) {
      const data = block.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n');
      if (!data) continue;
      try {
        const msg = JSON.parse(data);
        if (msg.id === id) return msg;
      } catch (_) { /* ignore keep-alives */ }
    }
    throw new Error('No response in event stream');
  }

  async function post(body, retried = false) {
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };
    if (mcp.sessionId) headers['Mcp-Session-Id'] = mcp.sessionId;
    if (mcp.protocol) headers['MCP-Protocol-Version'] = mcp.protocol;
    const res = await fetch(MCP_URL, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'same-origin' });
    const sid = res.headers.get('mcp-session-id');
    if (sid) mcp.sessionId = sid;
    if (res.status === 404 && mcp.sessionId && !retried && body.method !== 'initialize') {
      // Session expired on the server: reconnect once and retry.
      mcp.sessionId = null; mcp.protocol = null;
      await initialize();
      return post(body, true);
    }
    if (res.status === 202) return null;
    const text = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status}${text ? ': ' + text.slice(0, 160) : ''}`);
    const ct = res.headers.get('content-type') || '';
    return ct.includes('text/event-stream') ? parseSse(text, body.id) : JSON.parse(text);
  }

  async function rpc(method, params, label) {
    const body = { jsonrpc: '2.0', id: mcp.nextId++, method, params };
    const t0 = performance.now();
    let reply, error;
    try {
      reply = await post(body);
      if (reply && reply.error) error = new Error(reply.error.message || 'JSON-RPC error');
    } catch (e) { error = e; }
    const ms = performance.now() - t0;
    logEntry({ method, label, ms, request: body, response: reply || { error: String(error && error.message) }, failed: !!error || !!(reply && reply.result && reply.result.isError) });
    if (error) throw error;
    return reply.result;
  }

  async function initialize() {
    const result = await rpc('initialize', {
      protocolVersion: PROTOCOL,
      capabilities: {},
      clientInfo: { name: 'localpro-alexa-plus-simulator', version: '0.1.0' },
    });
    mcp.protocol = result.protocolVersion;
    await post({ jsonrpc: '2.0', method: 'notifications/initialized' });
    return result;
  }

  class ToolError extends Error {}

  async function callTool(name, args) {
    const result = await rpc('tools/call', { name, arguments: args }, name);
    if (result.isError) {
      const msg = (result.content || []).map((c) => c.text).filter(Boolean).join(' ') || 'Tool error';
      throw new ToolError(msg);
    }
    if (result.structuredContent) return result.structuredContent;
    const text = (result.content || []).find((c) => c.type === 'text');
    return text ? JSON.parse(text.text) : {};
  }

  // ---------------- UI helpers ----------------

  function setStatus(state, text) { els.status.dataset.state = state; els.statusText.textContent = text; }
  function setRing(state) { els.ring.dataset.state = state; }
  function scrollDown(node) { node.scrollTop = node.scrollHeight; }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function addMsg(role, text, opts = {}) {
    const li = document.createElement('li');
    li.className = `msg ${role}`;
    const who = role === 'alexa' ? 'Alexa' : role === 'user' ? 'You' : '';
    li.innerHTML = (who ? `<span class="who">${who}</span>` : '') + `<span class="body">${esc(text)}</span>`;
    if (opts.tools && opts.tools.length) {
      const wrap = document.createElement('div');
      wrap.className = 'tools';
      wrap.innerHTML = opts.tools.map((t) => `<span class="tool-tag">${esc(t)}</span>`).join('');
      li.appendChild(wrap);
    }
    if (opts.card) {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = opts.card.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
      li.appendChild(card);
    }
    els.transcript.appendChild(li);
    scrollDown(els.transcript);
    return li;
  }

  let voiceOn = 'speechSynthesis' in window;
  els.voice.setAttribute('aria-pressed', String(voiceOn));
  els.voice.textContent = voiceOn ? 'Voice on' : 'Voice off';
  if (!('speechSynthesis' in window)) els.voice.disabled = true;

  function speak(text) {
    if (!voiceOn || !('speechSynthesis' in window)) { setRing('idle'); return; }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/\$/g, ' dollars ').replace(/(\d+) dollars /g, '$1 dollars '));
    u.rate = 1.03;
    u.onstart = () => setRing('speaking');
    u.onend = u.onerror = () => setRing('idle');
    speechSynthesis.speak(u);
  }

  function alexaSays(text, opts) { addMsg('alexa', text, opts); speak(text); }

  function logEntry({ method, label, ms, request, response, failed }) {
    const li = document.createElement('li');
    const det = document.createElement('details');
    det.className = 'entry' + (failed ? ' err' : '');
    det.innerHTML =
      `<summary><span class="m">${esc(method)}</span><span class="n">${esc(label || '')}</span><span class="ms">${ms.toFixed(0)} ms</span></summary>` +
      `<pre><b>→ request</b>\n${esc(JSON.stringify(request, null, 2))}\n\n<b>← response</b>\n${esc(JSON.stringify(response, null, 2))}</pre>`;
    li.appendChild(det);
    els.log.appendChild(li);
    while (els.log.children.length > 60) els.log.removeChild(els.log.firstChild);
    scrollDown(els.log);
  }

  // ---------------- Dialogue manager (scripted agent) ----------------

  const KNOWN_CITIES = ['lincoln city', 'depoe bay', 'newport', 'otis', 'gleneden beach', 'waldport', 'neskowin'];
  const TRADE_RULES = [
    ['roofing', /\b(roof|roofing|roofer|shingle|gutter)/i],
    ['electrical', /\b(electric|electrician|outlet|wiring|breaker|spark|power is out|light switch)/i],
    ['hvac', /\b(furnace|hvac|heat pump|heating|no heat|a\/c|ac unit|air condition)/i],
    ['plumbing', /\b(plumb|pipe|leak|drain|water heater|toilet|faucet|sink|sewer|flood)/i],
    ['handyman', /\b(handyman|drywall|door|repair|fix|install|assemble)/i],
  ];
  const EMERGENCY = /\b(now|asap|urgent|emergency|today|tonight|right away|immediately|burst|flood|flooding|spark|sparking|smoke|leaking|no heat)\b/i;
  const YES = /\b(yes|yeah|yep|yup|sure|book( it)?|do it|confirm|go ahead|please do|sounds good)\b/i;
  const NO = /\b(no|nope|cancel|don't|do not|stop|never mind|nevermind)\b/i;
  const NEXT = /\b(another|other|next|different|someone else|second)\b/i;

  const state = {};
  function resetState() {
    Object.assign(state, { stage: 'idle', trade: null, city: null, urgency: 'standard', issue: '', pros: [], proIndex: 0, quote: null, booking: null, checkedIn: false });
  }

  function detectTrade(t) { for (const [trade, re] of TRADE_RULES) if (re.test(t)) return trade; return null; }
  function detectCity(t) {
    const low = t.toLowerCase();
    const known = KNOWN_CITIES.find((c) => low.includes(c));
    if (known) return known;
    const m = t.match(/\b(?:in|near|around)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)/);
    return m ? m[1] : null;
  }
  function titleCase(s) { return s.replace(/\b\w/g, (c) => c.toUpperCase()); }
  function detectIssue(t) {
    const cleaned = t.replace(/^\s*alexa[,\s]*/i, '').replace(/\s*(i need|can you|please|get me|find me).*/i, '').trim();
    return (cleaned || t).replace(/[.!?]+$/, '').slice(0, 140);
  }
  function detectDetails(t) {
    let m = t.match(/\bfor\s+([A-Za-z][A-Za-z.'-]*(?:\s[A-Z][A-Za-z.'-]*)?)\s*(?:,|\bat\b)\s*(.+)$/i);
    if (m) return { name: m[1].trim(), address: m[2].replace(/[.!]+$/, '').trim() };
    m = t.match(/^\s*(?:it'?s\s+|name is\s+)?([A-Za-z][A-Za-z.'-]*(?:\s[A-Z][A-Za-z.'-]*)?)\s*(?:,|\bat\b)\s*(\d.+)$/i);
    if (m) return { name: m[1].trim(), address: m[2].replace(/[.!]+$/, '').trim() };
    return null;
  }

  let busy = false;
  function setBusy(b) {
    busy = b; els.send.disabled = b; els.say.disabled = b;
    document.querySelectorAll('.chip, .test').forEach((x) => { x.disabled = b; });
    setRing(b ? 'thinking' : els.ring.dataset.state === 'thinking' ? 'idle' : els.ring.dataset.state);
  }

  async function handle(text) {
    addMsg('user', text);
    setBusy(true);
    try {
      await ready;
      await route(text);
    } catch (e) {
      if (e instanceof ToolError) alexaSays(`Sorry, ${e.message.charAt(0).toLowerCase()}${e.message.slice(1)}`);
      else { alexaSays('Sorry, I couldn’t reach the LocalPro service just now. Please try again.'); console.error(e); }
    } finally {
      setBusy(false);
      els.say.focus();
    }
  }

  async function route(text) {
    if (state.stage === 'confirm') {
      if (NEXT.test(text) && !YES.test(text)) return quoteNext();
      if (YES.test(text)) {
        const details = detectDetails(text);
        if (details) return book(details);
        state.stage = 'details';
        return alexaSays('Great. What name and street address should I put on the work order?');
      }
      if (NO.test(text)) {
        state.stage = 'idle';
        return alexaSays('Okay, I won’t book anything. Let me know if you need someone later.');
      }
      return alexaSays(`Should I book ${state.quote.pro_name}? You can say yes, no, or "another option."`);
    }
    if (state.stage === 'details') {
      const details = detectDetails(text);
      if (details) return book(details);
      return alexaSays('I need a name and an address, for example: "for Sam at 1200 SW Ocean Drive."');
    }
    if (state.stage === 'booked' && !detectTrade(text)) {
      return alexaSays(`You're all set. Work order ${state.booking.work_order_id} is booked with ${state.booking.pro_name}, ${state.booking.arrival_window}.`);
    }

    // New request (or filling in a missing trade/city).
    const trade = detectTrade(text) || state.trade;
    const city = detectCity(text) || state.city;
    if (state.stage !== 'collect' || !state.issue) state.issue = detectIssue(text);
    if (EMERGENCY.test(text)) state.urgency = 'emergency';
    else if (state.stage !== 'collect') state.urgency = 'standard';
    Object.assign(state, { trade, city, stage: 'collect' });
    if (!trade) return alexaSays('What kind of help do you need: a plumber, electrician, roofer, HVAC tech, or handyman?');
    if (!city) return alexaSays('Which city is the job in?');

    const found = await callTool('find_pros', { trade, city: titleCase(city), urgency: state.urgency });
    state.pros = found.pros || [];
    state.proIndex = 0;
    if (!state.pros.length) {
      state.stage = 'idle';
      return alexaSays(`${found.message} Want me to try a nearby city?`, { tools: ['find_pros'] });
    }
    const filtered = found.excluded_unlicensed ? ` I skipped ${found.excluded_unlicensed} contractor${found.excluded_unlicensed > 1 ? 's' : ''} without a verified license.` : '';
    await quoteCurrent(['find_pros'], filtered);
  }

  async function quoteCurrent(tools = [], prefix = '') {
    const pro = state.pros[state.proIndex];
    const q = await callTool('quote_job', { pro_id: pro.pro_id, issue: state.issue || state.trade, urgency: state.urgency });
    state.quote = q;
    state.stage = 'confirm';
    const lead = state.urgency === 'emergency' ? 'The soonest licensed pro is' : 'A top-rated licensed pro is';
    const [lo, hi] = q.estimate_range_usd;
    alexaSays(
      `${lead} ${q.pro_name}, rated ${pro.rating}. They can arrive ${q.arrival_window}. ` +
      `The estimate is $${lo} to $${hi}, plus a $${q.call_out_fee_usd} call-out fee.${prefix ? ' ' + prefix.trim() : ''} Should I book it?`,
      {
        tools: [...tools, 'quote_job'],
        card: [
          ['Pro', `${q.pro_name} · ${pro.rating}★ (${pro.reviews})`],
          ['License', pro.license_no || 'Verified'],
          ['Arrival', q.arrival_window],
          ['Estimate', `$${lo}–$${hi} + $${q.call_out_fee_usd} call-out`],
          ['Quote', `${q.quote_id} · held 30 min`],
        ],
      },
    );
  }

  async function quoteNext() {
    if (state.proIndex + 1 >= state.pros.length) {
      return alexaSays(`${state.quote.pro_name} is the only licensed option I found. Should I book them?`);
    }
    state.proIndex += 1;
    await quoteCurrent();
  }

  async function book({ name, address }) {
    const city = state.city ? titleCase(state.city) : '';
    const fullAddress = city && !address.toLowerCase().includes(city.toLowerCase()) ? `${address}, ${city}, OR` : address;
    const b = await callTool('book_job', { quote_id: state.quote.quote_id, customer_name: name, service_address: fullAddress });
    state.booking = b; state.stage = 'booked'; state.checkedIn = false;
    alexaSays(
      `Done. ${b.pro_name} is booked for ${b.arrival_window}. Your work order number is ${b.work_order_id.replace('WO-', '')}. ` +
      `The technician has to show a signed authorization when they arrive, so you'll know it's really them.`,
      { tools: ['book_job'], card: [['Work order', b.work_order_id], ['Customer', name], ['Address', fullAddress], ['Arrival', b.arrival_window]] },
    );
    showTech(b);
  }

  // ---------------- Technician panel ----------------

  function showTech(b) {
    els.techEmpty.hidden = true; els.techBody.hidden = false;
    els.woLabel.textContent = 'Signed with HMAC-SHA256';
    els.woId.textContent = b.work_order_id;
    els.woPro.textContent = `${b.pro_name} (${b.pro_id})`;
    els.woWindow.textContent = b.arrival_window;
    els.woToken.textContent = b.authorization_token;
    els.verdicts.innerHTML = '';
  }

  function verdict(ok, title, detail) {
    const li = document.createElement('li');
    li.className = `verdict ${ok ? 'ok' : 'bad'}`;
    li.innerHTML = `<b>${ok ? 'Accepted' : 'Rejected'}</b><span><strong>${esc(title)}.</strong> ${esc(detail)}</span>`;
    els.verdicts.prepend(li);
  }

  function tamper(token) {
    const [payload, sig] = token.split('.');
    const i = Math.floor(payload.length / 2);
    const swapped = payload[i] === 'A' ? 'B' : 'A';
    return `${payload.slice(0, i)}${swapped}${payload.slice(i + 1)}.${sig}`;
  }

  async function runTest(kind) {
    const b = state.booking;
    if (!b || busy) return;
    setBusy(true);
    try {
      await ready;
      if (kind === 'checkin' || (kind === 'reuse' && !state.checkedIn)) {
        const r = await callTool('verify_work_order', { authorization_token: b.authorization_token, pro_id: b.pro_id, check_in: true });
        if (r.verified) state.checkedIn = true;
        verdict(r.verified, 'Booked pro checks in', r.verified ? `${r.work_order.pro_name} verified and checked in.` : r.reason);
        if (kind === 'checkin') return;
      }
      if (kind === 'reuse') {
        const r = await callTool('verify_work_order', { authorization_token: b.authorization_token, pro_id: b.pro_id, check_in: true });
        verdict(r.verified, 'Same token used again', r.reason);
      } else if (kind === 'wrong') {
        const other = b.pro_id === 'pro-102' ? 'pro-101' : 'pro-102';
        const r = await callTool('verify_work_order', { authorization_token: b.authorization_token, pro_id: other });
        verdict(r.verified, `Presented by ${other}`, r.reason);
      } else if (kind === 'tamper') {
        const r = await callTool('verify_work_order', { authorization_token: tamper(b.authorization_token), pro_id: b.pro_id });
        verdict(r.verified, 'One character changed', r.reason);
      }
    } catch (e) {
      verdict(false, 'Error', e.message);
    } finally {
      setBusy(false);
    }
  }

  // ---------------- Wiring ----------------

  function greet() {
    els.transcript.innerHTML = '';
    addMsg('system', 'Try a suggestion below, type a request, or use the mic.');
  }

  els.composer.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = els.say.value.trim();
    if (!text || busy) return;
    els.say.value = '';
    handle(text);
  });
  els.chips.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip || busy) return;
    resetState();
    handle(chip.dataset.say);
  });
  document.querySelectorAll('.test').forEach((btn) => btn.addEventListener('click', () => runTest(btn.dataset.test)));
  els.reset.addEventListener('click', () => {
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    resetState(); greet(); setRing('idle');
    els.techEmpty.hidden = false; els.techBody.hidden = true; els.woLabel.textContent = 'No work order yet';
  });
  els.voice.addEventListener('click', () => {
    voiceOn = !voiceOn;
    if (!voiceOn && 'speechSynthesis' in window) speechSynthesis.cancel();
    els.voice.setAttribute('aria-pressed', String(voiceOn));
    els.voice.textContent = voiceOn ? 'Voice on' : 'Voice off';
  });

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Recognition) {
    const rec = new Recognition();
    rec.lang = 'en-US'; rec.interimResults = false; rec.maxAlternatives = 1;
    rec.onstart = () => { els.mic.dataset.on = 'true'; setRing('listening'); };
    rec.onend = () => { els.mic.dataset.on = 'false'; if (els.ring.dataset.state === 'listening') setRing('idle'); };
    rec.onresult = (ev) => { const said = ev.results[0][0].transcript; if (said && !busy) handle(said); };
    els.mic.addEventListener('click', () => { if (!busy) { try { rec.start(); } catch (_) { /* already listening */ } } });
  } else {
    els.mic.hidden = true;
  }

  resetState();
  greet();
  els.endpoint.textContent = MCP_URL.replace(/^https?:\/\//, '');

  const ready = (async () => {
    try {
      const init = await initialize();
      const tools = await rpc('tools/list', {});
      const names = (tools.tools || []).map((t) => t.name);
      setStatus('ok', `Connected · MCP ${init.protocolVersion} · ${names.length} tools`);
      return true;
    } catch (e) {
      setStatus('error', 'MCP server unreachable');
      console.error(e);
      throw e;
    }
  })();
  ready.catch(() => {});
})();
