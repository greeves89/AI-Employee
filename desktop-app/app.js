/* AI-Employee Desktop — client logic
   Wires the static UI to the orchestrator REST + WebSocket APIs.
   No build step required: plain ES modules-free browser JS. */

'use strict';

/* ─────────────────────────  State  ───────────────────────── */
const S = {
  url: '',            // base server url, e.g. https://server.ai-employee.de
  token: '',          // JWT access token
  email: '',
  agents: [],
  agent: null,        // selected agent object
  sessionId: null,    // current chat session id
  ws: null,           // chat websocket
  streaming: false,   // assistant is currently answering
  voiceWs: null,
  recorder: null,
  recording: false,
};

const LS_KEY = 'ai-employee-desktop';

/* ─────────────────────────  Helpers  ───────────────────────── */
const $ = (id) => document.getElementById(id);
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

function saveSession() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ url: S.url, token: S.token, email: S.email }));
  } catch (_) {}
}
function loadSession() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return false;
    const d = JSON.parse(raw);
    if (d && d.token && d.url) { S.url = d.url; S.token = d.token; S.email = d.email || ''; return true; }
  } catch (_) {}
  return false;
}
function clearSession() {
  try { localStorage.removeItem(LS_KEY); } catch (_) {}
  S.token = ''; S.agent = null; S.sessionId = null;
}

function normalizeUrl(u) {
  u = (u || '').trim().replace(/\/+$/, '');
  if (u && !/^https?:\/\//i.test(u)) u = 'https://' + u;
  return u;
}
function wsBase() {
  return S.url.replace(/^http/i, 'ws');
}

async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  const res = await fetch(S.url + path, Object.assign({}, opts, { headers }));
  if (res.status === 401) { clearSession(); showScreen('login'); throw new Error('Sitzung abgelaufen. Bitte neu anmelden.'); }
  if (!res.ok) {
    let msg = 'HTTP ' + res.status;
    try { const j = await res.json(); msg = j.detail || j.message || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

/* ─────────────────────────  Screens  ───────────────────────── */
function showScreen(name) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('active'));
  const scr = $('screen-' + name);
  if (scr) scr.classList.add('active');
}

/* ─────────────────────────  Login  ───────────────────────── */
async function doLogin() {
  const btn = $('btn-login');
  const err = $('login-err');
  err.textContent = '';
  const url = normalizeUrl($('in-url').value);
  const email = $('in-email').value.trim();
  const pass = $('in-pass').value;
  if (!url || !email || !pass) { err.textContent = 'Bitte alle Felder ausfüllen.'; return; }

  S.url = url;
  btn.disabled = true; btn.textContent = 'Anmelden…';
  try {
    const data = await api('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password: pass }),
    });
    S.token = data.access_token || data.token || '';
    if (!S.token) throw new Error('Kein Token vom Server erhalten.');
    S.email = email;
    saveSession();
    await enterApp();
  } catch (e) {
    err.textContent = e.message || 'Anmeldung fehlgeschlagen.';
  } finally {
    btn.disabled = false; btn.textContent = 'Anmelden';
  }
}

async function enterApp() {
  $('conn-server').textContent = S.url.replace(/^https?:\/\//, '');
  showScreen('agents');
  await loadAgents();
}

function doLogout() {
  closeChatWs();
  clearSession();
  showScreen('login');
  $('in-pass').value = '';
}

/* ─────────────────────────  Agents  ───────────────────────── */
async function loadAgents() {
  const grid = $('agent-grid');
  grid.innerHTML = '<div class="loading">Lade Agenten…</div>';
  try {
    const data = await api('/api/v1/agents/');
    S.agents = Array.isArray(data) ? data : (data.agents || data.items || []);
    renderAgents();
  } catch (e) {
    grid.innerHTML = '<div class="loading">Fehler: ' + escapeHtml(e.message) + '</div>';
  }
}

function agentEmoji(a) {
  if (a.avatar && /\p{Emoji}/u.test(a.avatar)) return a.avatar;
  const map = { developer: '💻', coder: '💻', designer: '🎨', researcher: '🔬', writer: '✍️', assistant: '🤖', analyst: '📊', support: '🎧' };
  const key = (a.role || '').toLowerCase();
  for (const k in map) if (key.includes(k)) return map[k];
  return '🤖';
}

function renderAgents() {
  const grid = $('agent-grid');
  grid.innerHTML = '';
  if (!S.agents.length) { grid.innerHTML = '<div class="loading">Keine Agenten verfügbar.</div>'; return; }
  S.agents.forEach((a) => {
    const card = el('div', 'acard');
    const online = (a.status || '').toLowerCase() === 'online' || a.is_active || a.active;
    card.innerHTML =
      '<div class="av">' + agentEmoji(a) + '</div>' +
      '<div class="name">' + escapeHtml(a.name || 'Agent') + '</div>' +
      '<div class="role">' + escapeHtml(a.role || '') + '</div>' +
      '<div class="st ' + (online ? '' : 'off') + '"><i></i>' + (online ? 'Online' : 'Offline') + '</div>';
    card.addEventListener('click', () => openAgent(a));
    grid.appendChild(card);
  });
}

/* ─────────────────────────  Chat: open agent  ───────────────────────── */
async function openAgent(a) {
  S.agent = a;
  S.sessionId = null;
  $('cur-av').textContent = agentEmoji(a);
  $('cur-name').textContent = a.name || 'Agent';
  $('chat-title').textContent = 'Neuer Chat';
  $('chat-sub').textContent = a.role || '';
  $('thread').innerHTML = '<div class="welcome">Schreib eine Nachricht, um mit ' + escapeHtml(a.name || 'dem Agenten') + ' zu starten.</div>';
  showScreen('chat');
  await loadSessions();
}

/* ─────────────────────────  Chat sessions (sidebar)  ───────────────────────── */
async function loadSessions() {
  const list = $('chat-list');
  list.innerHTML = '<div class="empty">Lade…</div>';
  try {
    const data = await api('/api/v1/agents/' + S.agent.id + '/chat/sessions');
    const sessions = data.sessions || data || [];
    renderSessions(sessions);
  } catch (e) {
    list.innerHTML = '<div class="empty">Keine Chats.</div>';
  }
}

function renderSessions(sessions) {
  const list = $('chat-list');
  list.innerHTML = '';
  if (!sessions.length) { list.innerHTML = '<div class="empty">Noch keine Chats. Starte einen neuen!</div>'; return; }
  sessions.forEach((s) => {
    const row = el('div', 'crow');
    if (s.session_id === S.sessionId) row.classList.add('sel');
    const title = s.preview ? s.preview.slice(0, 40) : ('Chat ' + (s.session_id || '').slice(0, 6));
    row.innerHTML =
      '<div class="t">' + escapeHtml(title) + '</div>' +
      '<div class="s"><span>' + fmtTime(s.last_message_at || s.started_at) + '</span><span>' + (s.message_count || 0) + '</span></div>';
    row.addEventListener('click', () => openSession(s.session_id));
    list.appendChild(row);
  });
}

async function openSession(sessionId) {
  S.sessionId = sessionId;
  document.querySelectorAll('.crow').forEach((r) => r.classList.remove('sel'));
  await loadHistory(sessionId);
  await loadSessions();
}

/* ─────────────────────────  Chat history  ───────────────────────── */
async function loadHistory(sessionId) {
  const thread = $('thread');
  thread.innerHTML = '<div class="welcome">Lade Verlauf…</div>';
  try {
    const data = await api('/api/v1/agents/' + S.agent.id + '/chat/history?session_id=' + encodeURIComponent(sessionId));
    const msgs = data.messages || [];
    thread.innerHTML = '';
    if (!msgs.length) { thread.innerHTML = '<div class="welcome">Leerer Chat.</div>'; return; }
    msgs.forEach((m) => appendMessage(m.role, m.content));
    scrollThread();
    const first = msgs.find((m) => m.role === 'user');
    if (first) $('chat-title').textContent = first.content.slice(0, 50);
  } catch (e) {
    thread.innerHTML = '<div class="welcome">Verlauf konnte nicht geladen werden.</div>';
  }
}

/* ─────────────────────────  New chat  ───────────────────────── */
function newChat() {
  closeChatWs();
  S.sessionId = null;
  $('chat-title').textContent = 'Neuer Chat';
  $('thread').innerHTML = '<div class="welcome">Schreib eine Nachricht, um zu starten.</div>';
  document.querySelectorAll('.crow').forEach((r) => r.classList.remove('sel'));
  $('input-msg').focus();
}

/* ─────────────────────────  Messages UI  ───────────────────────── */
function appendMessage(role, content) {
  const thread = $('thread');
  const welcome = thread.querySelector('.welcome');
  if (welcome) welcome.remove();
  const msg = el('div', 'msg ' + (role === 'user' ? 'user' : 'assistant'));
  const av = el('div', 'av');
  av.textContent = role === 'user' ? (S.email ? S.email[0].toUpperCase() : 'U') : agentEmoji(S.agent || {});
  const bub = el('div', 'bub');
  bub.textContent = content || '';
  msg.appendChild(av); msg.appendChild(bub);
  thread.appendChild(msg);
  scrollThread();
  return bub;
}

function scrollThread() {
  const t = $('thread');
  t.scrollTop = t.scrollHeight;
}

/* ─────────────────────────  Chat WebSocket  ───────────────────────── */
function closeChatWs() {
  if (S.ws) { try { S.ws.close(); } catch (_) {} S.ws = null; }
  S.streaming = false;
}

function ensureChatWs() {
  return new Promise((resolve, reject) => {
    if (S.ws && S.ws.readyState === WebSocket.OPEN) return resolve(S.ws);
    closeChatWs();
    const url = wsBase() + '/api/v1/ws/agents/' + S.agent.id + '/chat?token=' + encodeURIComponent(S.token);
    const ws = new WebSocket(url);
    let curBub = null;

    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error('Verbindung zum Chat fehlgeschlagen.'));
    ws.onclose = () => { if (S.ws === ws) S.ws = null; };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      const type = msg.type;
      const d = msg.data || {};
      if (type === 'ready') {
        curBub = appendMessage('assistant', '');
        curBub.innerHTML = '<span class="cursor"></span>';
        curBub._text = '';
      } else if (type === 'session') {
        if (d.session_id) S.sessionId = d.session_id;
      } else if (type === 'text') {
        if (!curBub) { curBub = appendMessage('assistant', ''); curBub._text = ''; }
        curBub._text = (curBub._text || '') + (d.text || '');
        curBub.textContent = curBub._text;
        scrollThread();
      } else if (type === 'tool' || type === 'tool_call') {
        const chip = el('div', 'tool-chip');
        chip.innerHTML = '⚙ <b>' + escapeHtml(d.name || d.tool || 'Tool') + '</b>';
        curBub ? curBub.parentElement.appendChild(chip) : $('thread').appendChild(chip);
        scrollThread();
      } else if (type === 'security_block') {
        appendMessage('assistant', '🛡 Aktion blockiert: ' + (d.reason || 'Sicherheitsregel'));
        S.streaming = false; setComposerBusy(false);
      } else if (type === 'cancelled') {
        S.streaming = false; setComposerBusy(false);
      } else if (type === 'done') {
        if (curBub) { curBub.textContent = curBub._text || curBub.textContent; }
        curBub = null;
        S.streaming = false;
        setComposerBusy(false);
        loadSessions();
      } else if (type === 'error') {
        appendMessage('assistant', '⚠ Fehler: ' + (d.message || d.error || 'Unbekannt'));
        S.streaming = false; setComposerBusy(false);
      }
    };
  });
}

async function sendMessage() {
  const input = $('input-msg');
  const text = input.value.trim();
  if (!text || S.streaming || !S.agent) return;

  appendMessage('user', text);
  if ($('chat-title').textContent === 'Neuer Chat') $('chat-title').textContent = text.slice(0, 50);
  input.value = '';
  autoGrow(input);

  S.streaming = true;
  setComposerBusy(true);
  try {
    const ws = await ensureChatWs();
    ws.send(JSON.stringify({ text, session_id: S.sessionId, source: 'webapp' }));
  } catch (e) {
    appendMessage('assistant', '⚠ ' + e.message);
    S.streaming = false; setComposerBusy(false);
  }
}

function stopStreaming() {
  if (S.ws && S.ws.readyState === WebSocket.OPEN) {
    try { S.ws.send(JSON.stringify({ action: 'stop' })); } catch (_) {}
  }
  S.streaming = false;
  setComposerBusy(false);
}

function setComposerBusy(busy) {
  $('btn-send').disabled = busy;
}

/* ─────────────────────────  Voice  ───────────────────────── */
function openVoice() {
  $('voice-overlay').classList.add('show');
  $('voice-title').textContent = 'Tippe zum Sprechen';
  $('voice-sub').textContent = '';
}
function closeVoice() {
  $('voice-overlay').classList.remove('show');
  if (S.recording) stopRecording();
}

async function toggleRecording() {
  if (S.recording) { stopRecording(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    S.recorder = new MediaRecorder(stream);
    const chunks = [];
    S.recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    S.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: 'audio/webm' });
      sendVoice(blob);
    };
    S.recorder.start();
    S.recording = true;
    $('voice-orb').classList.add('listening');
    $('voice-rec').classList.add('rec');
    $('voice-rec').textContent = '⏹ Stopp';
    $('voice-title').textContent = 'Ich höre zu…';
    $('btn-mic').classList.add('rec');
  } catch (e) {
    $('voice-sub').textContent = 'Mikrofon nicht verfügbar: ' + e.message;
  }
}

function stopRecording() {
  if (S.recorder && S.recorder.state !== 'inactive') S.recorder.stop();
  S.recording = false;
  $('voice-orb').classList.remove('listening');
  $('voice-rec').classList.remove('rec');
  $('voice-rec').textContent = '🎙 Aufnehmen';
  $('voice-title').textContent = 'Verarbeite…';
  $('btn-mic').classList.remove('rec');
}

async function sendVoice(blob) {
  // Send audio to the agent voice WS; fall back to attaching as a chat upload.
  try {
    const url = wsBase() + '/api/v1/ws/agents/' + S.agent.id + '/voice?token=' + encodeURIComponent(S.token);
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    ws.onopen = async () => {
      const buf = await blob.arrayBuffer();
      ws.send(buf);
      ws.send(JSON.stringify({ action: 'flush', session_id: S.sessionId, source: 'webapp' }));
    };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (msg.type === 'transcript' && msg.data && msg.data.text) {
        $('voice-sub').textContent = msg.data.text;
        $('input-msg').value = msg.data.text;
      } else if (msg.type === 'text' && msg.data) {
        $('voice-title').textContent = 'Antwort';
        $('voice-sub').textContent = (msg.data.text || '');
      } else if (msg.type === 'done') {
        ws.close();
        closeVoice();
      }
    };
    ws.onerror = () => { $('voice-sub').textContent = 'Voice-Verbindung fehlgeschlagen.'; };
  } catch (e) {
    $('voice-sub').textContent = 'Fehler: ' + e.message;
  }
}

/* ─────────────────────────  Utils  ───────────────────────── */
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
}
function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
}

/* ─────────────────────────  Wiring  ───────────────────────── */
function bind() {
  $('btn-login').addEventListener('click', doLogin);
  $('in-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  $('btn-logout').addEventListener('click', doLogout);
  $('btn-back-agents').addEventListener('click', () => { closeChatWs(); showScreen('agents'); loadAgents(); });
  $('btn-newchat').addEventListener('click', newChat);
  $('btn-send').addEventListener('click', () => { S.streaming ? stopStreaming() : sendMessage(); });

  const input = $('input-msg');
  input.addEventListener('input', () => autoGrow(input));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  $('btn-mic').addEventListener('click', openVoice);
  $('voice-rec').addEventListener('click', toggleRecording);
  $('voice-close').addEventListener('click', closeVoice);
}

async function init() {
  bind();
  if (loadSession()) {
    $('in-url').value = S.url;
    $('in-email').value = S.email;
    try { await enterApp(); return; } catch (_) {}
  }
  showScreen('login');
  $('in-url').focus();
}

document.addEventListener('DOMContentLoaded', init);
