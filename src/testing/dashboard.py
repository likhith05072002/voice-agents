"""The live dual-caller dashboard — one self-contained HTML page."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Voice Agent — Live Call Tester</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0a0e14; color: #e6edf3;
         font: 14px/1.5 system-ui, "Segoe UI", sans-serif; }
  header { padding: 14px 24px; border-bottom: 1px solid #1c2333;
           display: flex; align-items: center; gap: 16px; }
  h1 { font-size: 17px; margin: 0; }
  .muted { color: #7d8590; font-size: 12px; }
  main { max-width: 1100px; margin: 0 auto; padding: 20px; }
  .controls { display: flex; gap: 12px; align-items: center; margin-bottom: 18px;
              background: #11161f; border: 1px solid #1c2333; border-radius: 12px;
              padding: 14px 18px; }
  button { background: #238636; color: #fff; border: 0; border-radius: 8px;
           padding: 11px 26px; font-size: 15px; font-weight: 700; cursor: pointer; }
  button:disabled { background: #30363d; cursor: wait; }
  select { background: #0a0e14; color: #e6edf3; border: 1px solid #2b3346;
           border-radius: 8px; padding: 10px 12px; font-size: 14px; }
  .state { padding: 3px 12px; border-radius: 20px; font-size: 12px;
           background: #1f6feb33; color: #58a6ff; }
  .state.done { background: #23863633; color: #3fb950; }
  .state.failed { background: #f8514933; color: #f85149; }

  /* ── the dual-caller split ── */
  .call { display: grid; grid-template-columns: 1fr 56px 1fr; gap: 0;
          margin-bottom: 18px; }
  .phone { background: #11161f; border: 1px solid #1c2333; border-radius: 16px;
           padding: 18px; min-height: 380px; display: flex; flex-direction: column;
           transition: box-shadow .2s, border-color .2s; }
  .phone.speaking { border-color: #3fb950; box-shadow: 0 0 24px #3fb95044; }
  .phone.tester.speaking { border-color: #d29922; box-shadow: 0 0 24px #d2992244; }
  .phone h3 { margin: 0 0 2px; font-size: 15px; }
  .phone .num { color: #7d8590; font-size: 12px; margin-bottom: 10px; }
  .avatar { font-size: 34px; margin-bottom: 6px; }
  .wave { height: 18px; display: flex; gap: 3px; align-items: flex-end;
          margin-bottom: 12px; visibility: hidden; }
  .speaking .wave { visibility: visible; }
  .wave i { width: 4px; background: #3fb950; border-radius: 2px;
            animation: wv .7s infinite ease-in-out; }
  .tester .wave i { background: #d29922; }
  .wave i:nth-child(2){animation-delay:.15s}.wave i:nth-child(3){animation-delay:.3s}
  .wave i:nth-child(4){animation-delay:.45s}.wave i:nth-child(5){animation-delay:.6s}
  @keyframes wv { 0%,100%{height:4px} 50%{height:18px} }
  .talk { flex: 1; overflow-y: auto; display: flex; flex-direction: column;
          gap: 8px; font-size: 13px; }
  .bubble { background: #1a2130; border-radius: 10px; padding: 8px 12px;
            max-width: 95%; }
  .tester .bubble { background: #2a2415; }
  .bubble .who { font-size: 10px; color: #7d8590; text-transform: uppercase; }
  .link { display: flex; flex-direction: column; align-items: center;
          justify-content: center; color: #58a6ff; font-size: 22px; }
  .link .lbl { font-size: 10px; color: #7d8590; margin-top: 6px; text-align: center; }

  section { background: #11161f; border: 1px solid #1c2333; border-radius: 12px;
            padding: 16px 20px; margin-bottom: 18px; }
  h2 { font-size: 13px; margin: 0 0 10px; color: #7d8590;
       text-transform: uppercase; letter-spacing: .06em; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #1c2333;
           vertical-align: top; }
  th { color: #7d8590; font-weight: 500; font-size: 12px; }
  .pass { color: #3fb950; font-weight: 700; }
  .fail { color: #f85149; font-weight: 700; }
  .lat { font-variant-numeric: tabular-nums; }
  audio { width: 100%; margin-top: 10px; }
  #events { max-height: 120px; overflow-y: auto; font-family: ui-monospace, monospace;
            font-size: 12px; color: #7d8590; white-space: pre-wrap; }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: #f85149;
              display: inline-block; margin-right: 6px; animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity: .2; } }
</style>
</head>
<body>
<header>
  <h1>📞 Live Call Tester</h1>
  <span class="muted" id="health">connecting…</span>
</header>
<main>
  <div class="controls">
    <select id="scenario"></select>
    <select id="transport">
      <option value="loopback">Loopback (free, instant)</option>
      <option value="pstn">REAL CALL (number → number)</option>
    </select>
    <button id="start" onclick="startTest()">▶ Start Test</button>
    <span id="teststate"></span>
    <span class="muted" id="greeting"></span>
    <span class="muted" id="livemark" style="display:none">
      <span class="live-dot"></span>LIVE AUDIO</span>
  </div>

  <div class="controls" style="border-color:#2b3a2b">
    <span style="font-weight:700">📞 Talk to your AI</span>
    <select id="callAgent"></select>
    <input id="myNumber" placeholder="+91XXXXXXXXXX" style="background:#0a0e14;
      color:#e6edf3;border:1px solid #2b3346;border-radius:8px;padding:10px 12px;
      font-size:14px;width:180px">
    <button id="callme" onclick="callMe()"
      style="background:#1f6feb">📞 Call My Phone</button>
    <span class="muted" id="callmestate"></span>
  </div>

  <div class="call">
    <div class="phone tester" id="phoneT">
      <div class="avatar">🤖</div>
      <h3>Tester Agent</h3>
      <div class="num" id="numT">caller · scripted questions</div>
      <div class="wave"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="talk" id="talkT"></div>
    </div>
    <div class="link">⇆<div class="lbl" id="linklbl">idle</div></div>
    <div class="phone" id="phoneA">
      <div class="avatar">🎙️</div>
      <h3>Main Agent</h3>
      <div class="num" id="numA">receptionist · production pipeline</div>
      <div class="wave"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="talk" id="talkA"></div>
    </div>
  </div>

  <section id="results" style="display:none">
    <h2>Step results</h2>
    <table id="steps">
      <thead><tr><th>#</th><th>Question</th><th>Latency</th><th>Barge-in stop</th>
      <th>Agent's answer</th><th>Check</th></tr></thead>
      <tbody></tbody>
    </table>
    <div id="audio"></div>
    <div id="events"></div>
  </section>

  <section>
    <h2>Recent calls <span class="muted">(auto-refresh)</span></h2>
    <table id="calls">
      <thead><tr><th>When</th><th>Agent</th><th>Dur</th><th>Turns</th>
      <th>Avg latency</th><th>Outcome</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
</main>
<script>
const $ = s => document.querySelector(s);
let testId = null, poller = null, audioWS = null, seenT = 0, seenA = 0;

async function j(url, opts) { const r = await fetch(url, opts); return r.json(); }
function esc(t){const d=document.createElement('div');d.textContent=t||'';return d.innerHTML}

async function init() {
  const h = await j('/health');
  $('#health').textContent = `agents: ${h.agents} · active calls: ${h.active_sessions}`;
  const s = await j('/test/scenarios');
  $('#scenario').innerHTML = s.scenarios.map(n => `<option>${n}</option>`).join('');
  const a = await j('/agents-lite');
  $('#callAgent').innerHTML = a.agents.map(x =>
    `<option value="${esc(x.agent_id)}">${esc(x.name)}</option>`).join('');
  $('#myNumber').value = localStorage.getItem('myNumber') || '';
  refreshCalls(); setInterval(refreshCalls, 6000);
}

async function callMe() {
  const to = $('#myNumber').value.trim();
  if (!/^\\+\\d{8,15}$/.test(to)) {
    $('#callmestate').textContent = 'enter number as +91XXXXXXXXXX'; return;
  }
  localStorage.setItem('myNumber', to);
  $('#callme').disabled = true;
  $('#callmestate').innerHTML = '<span class="live-dot"></span>calling your phone…';
  const r = await j('/test/call-me', {method:'POST',
    headers:{'content-type':'application/json'},
    body: JSON.stringify({to, agent_id: $('#callAgent').value})});
  if (r.error) { $('#callmestate').textContent = '❌ ' + r.error; }
  else { $('#callmestate').textContent =
    '📞 ringing — pick up and talk! (call appears in Recent calls after)'; }
  setTimeout(() => { $('#callme').disabled = false; }, 8000);
  setTimeout(refreshCalls, 20000);
}

function bubble(pane, who, text) {
  const el = document.createElement('div');
  el.className = 'bubble';
  el.innerHTML = `<div class="who">${who}</div>${esc(text)}`;
  pane.appendChild(el);
  pane.scrollTop = 1e9;
}

async function startTest() {
  $('#start').disabled = true;
  $('#talkT').innerHTML = ''; $('#talkA').innerHTML = '';
  $('#audio').innerHTML = ''; $('#events').textContent = '';
  seenT = 0; seenA = 0;
  const transport = $('#transport').value;
  const r = await j('/test/start', {method:'POST',
    headers:{'content-type':'application/json'},
    body: JSON.stringify({scenario: $('#scenario').value, transport})});
  if (r.error) { alert(r.error); $('#start').disabled = false; return; }
  testId = r.test_id;
  listenLive();
  poller = setInterval(poll, 400);
}

function listenLive() {
  // live mixed call audio -> speakers
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  let t = 0;
  audioWS = new WebSocket(
    (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
    '/test/listen/' + testId);
  audioWS.binaryType = 'arraybuffer';
  audioWS.onopen = () => $('#livemark').style.display = '';
  audioWS.onclose = () => $('#livemark').style.display = 'none';
  audioWS.onmessage = e => {
    const i16 = new Int16Array(e.data);
    const buf = ctx.createBuffer(1, i16.length, 8000);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination);
    if (t < ctx.currentTime + 0.05) t = ctx.currentTime + 0.08;
    src.start(t); t += buf.duration;
  };
}

async function poll() {
  const st = await j(`/test/status/${testId}`);
  $('#teststate').innerHTML = `<span class="state ${st.state}">${st.state}</span>`;
  $('#greeting').textContent = st.greeting_ms ? `greeting: ${st.greeting_ms} ms` : '';
  $('#linklbl').textContent = st.transport === 'pstn' ? 'REAL PSTN CALL' : 'loopback';

  // speaking glow
  $('#phoneT').classList.toggle('speaking', !!st.tester_speaking);
  $('#phoneA').classList.toggle('speaking', !!st.agent_speaking);

  // tester bubbles: questions as they're asked (from events)
  const asked = st.events.filter(e => e.msg.startsWith('asked:') ||
                                      e.msg.startsWith('BARGE-IN'));
  for (; seenT < asked.length; seenT++) {
    const m = asked[seenT].msg;
    bubble($('#talkT'), m.startsWith('BARGE') ? 'barge-in ⚡' : 'asks',
           m.replace(/^asked: |^BARGE-IN: talking over the agent: /, ''));
  }
  // main agent bubbles: live transcript (assistant side)
  const agentLines = (st.live_transcript || []).filter(x => x.role === 'assistant');
  for (; seenA < agentLines.length; seenA++)
    bubble($('#talkA'), 'says', agentLines[seenA].text);

  // results table
  $('#results').style.display = '';
  $('#steps tbody').innerHTML = st.steps.map((s,i)=>`<tr>
    <td>${i+1}</td><td>${esc(s.question)}</td>
    <td class="lat">${s.latency_ms!=null ? s.latency_ms+' ms' : (s.status==='pending'?'—':s.status)}</td>
    <td class="lat">${s.time_to_silence_ms!=null ? s.time_to_silence_ms+' ms' : ''}</td>
    <td>${esc(s.answer)}</td>
    <td class="${s.check}">${s.check==='none'?'':s.check.toUpperCase()}</td></tr>`).join('');
  $('#events').textContent = st.events.map(e=>e.msg).join('\\n');

  if (st.state === 'done' || st.state === 'failed') {
    clearInterval(poller); $('#start').disabled = false;
    if (audioWS) audioWS.close();
    $('#audio').innerHTML = `<audio controls src="/test/audio/${testId}"></audio>`;
    if (st.error) $('#events').textContent += '\\nERROR: ' + st.error;
    refreshCalls();
  }
}

async function refreshCalls() {
  const r = await j('/calls?limit=12');
  $('#calls tbody').innerHTML = (r.calls||[]).map((c,i)=>{
    const when = c.started_at ? new Date(c.started_at*1000).toLocaleTimeString() : '';
    return `<tr onclick="toggle(${i})" style="cursor:pointer">
      <td>${when}</td><td>${esc(c.agent_id)}</td>
      <td class="lat">${c.duration_s!=null ? c.duration_s.toFixed(0)+'s':''}</td>
      <td>${c.turn_count}</td>
      <td class="lat">${c.avg_perceived_ms!=null ? c.avg_perceived_ms+' ms':''}</td>
      <td>${esc(c.outcome)}</td></tr>
      <tr id="exp${i}" style="display:none"><td colspan="6">${transcript(c)}</td></tr>`;
  }).join('');
}

function transcript(c) {
  try {
    return JSON.parse(c.turns).map(t =>
      `<div><b style="color:${t.role==='user'?'#d29922':'#3fb950'}">${t.role}:</b> ${esc(t.text)}</div>`).join('');
  } catch { return ''; }
}
function toggle(i){const e=$(`#exp${i}`);e.style.display=e.style.display==='none'?'':'none'}
init();
</script>
</body>
</html>"""
