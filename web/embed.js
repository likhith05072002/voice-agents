/* SonusLabs website widget — drop-in "Talk to us" voice button.
 *
 * A client adds ONE tag to their site:
 *   <script src="https://sonuslabs.online/embed.js"
 *           data-agent="their-agent-id"
 *           data-label="Talk to reception"
 *           data-color="#0D9488"></script>
 *
 * No secret key in the browser: access is gated server-side by the agent being
 * embed-enabled AND this page's Origin being on the agent's allowlist. Usage
 * bills the agent owner's SonusLabs wallet. The audio path is the same proven
 * client the console uses (box-average mic downsample; sample-count playback).
 */
(function () {
  var me = document.currentScript;
  var AGENT = me.getAttribute("data-agent");
  var LABEL = me.getAttribute("data-label") || "Talk to us";
  var COLOR = me.getAttribute("data-color") || "#0D9488";
  if (!AGENT) { console.error("[SonusLabs] data-agent is required"); return; }

  // The widget talks to the origin that served this script.
  var API = new URL(me.src).origin;
  var WS = API.replace(/^http/, "ws");

  // ---------- styles ----------
  var css = document.createElement("style");
  css.textContent = [
    ".sl-fab{position:fixed;right:22px;bottom:22px;z-index:2147483000;display:flex;align-items:center;gap:10px;",
    "  border:none;border-radius:100px;padding:14px 20px;font:600 15px/1 system-ui,sans-serif;color:#fff;",
    "  cursor:pointer;box-shadow:0 12px 30px -8px rgba(0,0,0,.4);transition:transform .12s}",
    ".sl-fab:hover{transform:translateY(-2px)}",
    ".sl-fab .sl-dot{width:9px;height:9px;border-radius:50%;background:#fff;opacity:.9}",
    ".sl-panel{position:fixed;right:22px;bottom:88px;z-index:2147483000;width:320px;max-width:calc(100vw - 44px);",
    "  background:#0F1512;border:1px solid #23302c;border-radius:20px;overflow:hidden;color:#EAF2EF;",
    "  font-family:system-ui,sans-serif;box-shadow:0 30px 70px -20px rgba(0,0,0,.6);display:none}",
    ".sl-panel.on{display:block;animation:slup .22s ease}",
    "@keyframes slup{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}",
    ".sl-head{display:flex;align-items:center;gap:10px;padding:15px 16px;border-bottom:1px solid #23302c}",
    ".sl-head b{font-size:14.5px}.sl-head small{color:#8CA39D;font-size:12px;display:block}",
    ".sl-x{margin-left:auto;cursor:pointer;color:#8CA39D;font-size:20px;line-height:1}",
    ".sl-orbwrap{display:flex;flex-direction:column;align-items:center;padding:18px 16px 8px}",
    ".sl-orb{width:120px;height:120px;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;",
    "  color:#06231e;font:700 13px system-ui;position:relative;transition:box-shadow .15s}",
    ".sl-status{font-family:ui-monospace,monospace;font-size:11px;color:#8CA39D;margin-top:12px}",
    ".sl-caps{max-height:150px;overflow-y:auto;padding:8px 16px 16px;display:flex;flex-direction:column;gap:7px}",
    ".sl-cap{max-width:85%;padding:8px 11px;border-radius:13px;font-size:13px;line-height:1.4}",
    ".sl-cap.user{align-self:flex-end;color:#06231e;border-bottom-right-radius:4px}",
    ".sl-cap.assistant{align-self:flex-start;background:#1b2621;border-bottom-left-radius:4px}",
    ".sl-foot{text-align:center;font-size:10.5px;color:#5d726c;padding:0 0 12px}",
    ".sl-foot a{color:#7fb3a8;text-decoration:none}"
  ].join("");
  document.head.appendChild(css);

  // ---------- DOM ----------
  var fab = document.createElement("button");
  fab.className = "sl-fab"; fab.style.background = COLOR;
  fab.innerHTML = '<span class="sl-dot"></span>' + LABEL;

  var panel = document.createElement("div");
  panel.className = "sl-panel";
  panel.innerHTML =
    '<div class="sl-head"><b>Voice assistant</b><small>powered by SonusLabs</small><span class="sl-x">&times;</span></div>' +
    '<div class="sl-orbwrap">' +
      '<div class="sl-orb" id="sl-orb" style="background:radial-gradient(circle at 40% 35%,' + COLOR + ',#0a3a34)">Tap to talk</div>' +
      '<div class="sl-status" id="sl-status">tap the orb to start</div>' +
    '</div>' +
    '<div class="sl-caps" id="sl-caps"></div>' +
    '<div class="sl-foot">Powered by <a href="' + API + '" target="_blank" rel="noreferrer">SonusLabs</a></div>';
  document.body.appendChild(fab);
  document.body.appendChild(panel);

  var $ = function (id) { return panel.querySelector(id); };
  var orb = $("#sl-orb"), statusEl = $("#sl-status"), caps = $("#sl-caps");
  var open = false, ws = null, ctx = null, stream = null, proc = null, level = 0;

  fab.onclick = function () {
    open = !open; panel.classList.toggle("on", open);
    fab.style.display = open ? "none" : "flex";
  };
  panel.querySelector(".sl-x").onclick = function () {
    open = false; panel.classList.remove("on"); fab.style.display = "flex"; if (ws) stop();
  };

  function setStatus(t, c) { statusEl.textContent = t; if (c) statusEl.style.color = c; }
  function addCap(role, text) {
    var d = document.createElement("div"); d.className = "sl-cap " + role;
    if (role === "user") d.style.background = COLOR;
    d.textContent = text; caps.appendChild(d); caps.scrollTop = 1e9;
  }

  // pulse the orb from the audio level
  (function pulse() {
    orb.style.boxShadow = "0 0 0 " + (level * 26) + "px " + hexA(COLOR, 0.18) +
      ", 0 0 0 " + (level * 46) + "px " + hexA(COLOR, 0.08);
    level *= 0.9; requestAnimationFrame(pulse);
  })();
  function hexA(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }

  orb.onclick = function () { ws ? stop() : start(); };

  function start() {
    setStatus("connecting…", "#e6b34d"); orb.textContent = "…"; caps.innerHTML = "";
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then(function (ms) {
        stream = ms; ctx = new (window.AudioContext || window.webkitAudioContext)();
        ws = new WebSocket(WS + "/web-call?agent_id=" + encodeURIComponent(AGENT));
        ws.binaryType = "arraybuffer";
        ws.onopen = function () { setStatus("● live", "#5eead4"); orb.textContent = "Listening"; };
        ws.onclose = function () { if (ws) stop(); };
        ws.onerror = function () { if (ws) stop(); };
        var ps = 0, pe = 0;
        ws.onmessage = function (e) {
          if (typeof e.data === "string") {
            var m = JSON.parse(e.data);
            if (m.type === "call_end") { setStatus(m.reason === "no_credits" ? "out of credits" : "call ended"); stop(); return; }
            if (m.type) return;
            if (m.text && m.text.trim()) addCap(m.role, m.text);
            return;
          }
          var i16 = new Int16Array(e.data), buf = ctx.createBuffer(1, i16.length, 16000), ch = buf.getChannelData(0), pk = 0;
          for (var i = 0; i < i16.length; i++) { var s = i16[i] / 32768; ch[i] = s; var av = s < 0 ? -s : s; if (av > pk) pk = av; }
          var src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination);
          var t = pe + ps / 16000; if (t < ctx.currentTime + 0.02) { pe = ctx.currentTime + 0.25; ps = 0; t = pe; }
          src.start(t); ps += i16.length; level = pk;
        };
        var source = ctx.createMediaStreamSource(ms), node = ctx.createScriptProcessor(4096, 1, 1); proc = node;
        var ratio = ctx.sampleRate / 16000;
        node.onaudioprocess = function (ev) {
          if (!ws || ws.readyState !== 1) return;
          var inp = ev.inputBuffer.getChannelData(0), out = new Int16Array(Math.floor(inp.length / ratio)), pk = 0;
          for (var i = 0; i < out.length; i++) {
            var a = Math.floor(i * ratio), b = Math.floor((i + 1) * ratio), sum = 0;
            for (var k = a; k < b; k++) sum += inp[k];
            var v = sum / Math.max(1, b - a); out[i] = Math.max(-32768, Math.min(32767, v * 32768));
            var av = v < 0 ? -v : v; if (av > pk) pk = av;
          }
          ws.send(out.buffer); if (pk > 0.06) level = pk;
        };
        source.connect(node); var mute = ctx.createGain(); mute.gain.value = 0; node.connect(mute); mute.connect(ctx.destination);
      })
      .catch(function () { setStatus("microphone blocked", "#fb7185"); orb.textContent = "Tap to talk"; });
  }

  function stop() {
    var s = ws; ws = null; if (s && s.readyState <= 1) s.close();
    if (proc) { proc.disconnect(); proc = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (ctx) { ctx.close().catch(function () {}); ctx = null; }
    level = 0; setStatus("tap the orb to start"); orb.textContent = "Tap to talk";
  }
})();
