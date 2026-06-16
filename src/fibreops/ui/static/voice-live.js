/* Voice Live realtime client.
 *
 * Talks the OpenAI-Realtime-compatible event protocol that Azure Voice Live
 * exposes, through the server-side proxy at /ws/voice. Two entry points:
 *
 *   voiceLive.speak(text, opts)   — one-shot TTS for the "Speak status" button.
 *   voiceLive.toggleMic()         — duplex mic for the "Talk to agent" button.
 *
 * Audio out: PCM16 mono @ 24 kHz, decoded via WebAudio.
 * Audio in:  mic captured via getUserMedia, downsampled+encoded to PCM16
 *            mono @ 24 kHz, base64-framed into input_audio_buffer.append.
 */
(function () {
  const SAMPLE_RATE = 24000;

  let session = null;        // {enabled, ws_path, voice, agent_id, duplex_enabled}
  let ws = null;
  let audioCtx = null;
  let nextPlayTime = 0;
  let mode = "idle";          // "idle" | "speak" | "mic"
  let mic = null;             // {stream, source, processor}
  let micActive = false;

  function setStatus(text, kind) {
    const el = document.getElementById("voice-status");
    if (!el) return;
    el.textContent = text;
    el.dataset.kind = kind || "info";
  }

  function getCtx() {
    if (!audioCtx || audioCtx.state === "closed") {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctor({ sampleRate: SAMPLE_RATE });
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function pcm16ToFloat32(buf) {
    const view = new DataView(buf);
    const out = new Float32Array(buf.byteLength / 2);
    for (let i = 0; i < out.length; i++) {
      const s = view.getInt16(i * 2, true);
      out[i] = s < 0 ? s / 0x8000 : s / 0x7fff;
    }
    return out;
  }

  function float32ToPcm16(input) {
    const out = new ArrayBuffer(input.length * 2);
    const view = new DataView(out);
    for (let i = 0; i < input.length; i++) {
      let s = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return out;
  }

  function bytesToBase64(bytes) {
    let bin = "";
    const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
    return btoa(bin);
  }

  function base64ToBytes(b64) {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr;
  }

  function playPcm16(b64) {
    const ctx = getCtx();
    const bytes = base64ToBytes(b64);
    const floats = pcm16ToFloat32(bytes.buffer);
    if (!floats.length) return;
    const buffer = ctx.createBuffer(1, floats.length, SAMPLE_RATE);
    buffer.copyToChannel(floats, 0);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const now = ctx.currentTime;
    const startAt = Math.max(now, nextPlayTime);
    src.start(startAt);
    nextPlayTime = startAt + buffer.duration;
  }

  async function loadSession() {
    if (session) return session;
    try {
      const r = await fetch("/api/voice/session");
      session = await r.json();
    } catch (e) {
      session = { enabled: false };
    }
    return session;
  }

  function ensureWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return Promise.resolve(ws);
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = proto + "//" + location.host + session.ws_path;
    ws = new WebSocket(url);
    return new Promise((resolve, reject) => {
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        sendSessionUpdate();
        resolve(ws);
      };
      ws.onerror = (e) => {
        setStatus("Voice Live error", "error");
        reject(e);
      };
      ws.onclose = () => {
        if (mode !== "idle") setStatus("Voice Live disconnected", "warn");
        mode = "idle";
        ws = null;
      };
      ws.onmessage = onMessage;
    });
  }

  function sendSessionUpdate() {
    // Voice must be a structured object for Voice Live API
    const voiceObj = session.agent_id
      ? session.voice  // custom agent may override voice config
      : { name: session.voice, type: session.voice_type || "azure-standard" };
    const sess = {
      modalities: mode === "mic" ? ["audio", "text"] : ["audio"],
      voice: voiceObj,
      input_audio_format: "pcm16",
      output_audio_format: "pcm16",
      input_audio_sampling_rate: 24000,
    };
    if (mode === "mic") {
      // Mic-only fields. server_echo_cancellation requires turn detection.
      sess.turn_detection = {
        type: "azure_semantic_vad",
        threshold: 0.5,
        prefix_padding_ms: 300,
        silence_duration_ms: 500,
      };
      sess.input_audio_noise_reduction = { type: "azure_deep_noise_suppression" };
      sess.input_audio_echo_cancellation = { type: "server_echo_cancellation" };
      sess.input_audio_transcription = { model: "azure-speech", language: "en" };
    }
    send({ type: "session.update", session: sess });
  }

  function send(obj) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(obj));
  }

  function onMessage(evt) {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    switch (msg.type) {
      case "session.created":
      case "session.updated":
        if (mode === "speak") setStatus("Speaking…", "ok");
        if (mode === "mic") setStatus("Listening — speak now", "ok");
        break;
      case "response.audio.delta":
      case "response.output_audio.delta":
        if (msg.delta) playPcm16(msg.delta);
        break;
      case "response.done":
      case "response.completed":
        if (mode === "speak") {
          setStatus("Idle", "info");
          mode = "idle";
          try { ws && ws.close(); } catch {}
        }
        break;
      case "error":
        console.error("Voice Live error", msg);
        setStatus("Voice Live: " + (msg.error?.message || "error"), "error");
        break;
    }
  }

  function speakBrowserTts(text) {
    if (!window.speechSynthesis) return false;
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = "en-GB";
    utt.rate = 1.05;
    // Prefer a British voice if available, else default.
    const voices = window.speechSynthesis.getVoices();
    const gb = voices.find((v) => v.lang === "en-GB") ||
                voices.find((v) => v.lang.startsWith("en"));
    if (gb) utt.voice = gb;
    utt.onstart = () => setStatus("Speaking…", "ok");
    utt.onend = () => setStatus("Idle", "info");
    utt.onerror = () => setStatus("Speech error", "error");
    window.speechSynthesis.speak(utt);
    return true;
  }

  async function speak(text, opts) {
    opts = opts || {};
    await loadSession();
    if (!session.enabled) {
      // No Voice Live endpoint — use browser built-in TTS as demo fallback.
      return speakBrowserTts(text);
    }
    if (!text || !text.trim()) return false;
    nextPlayTime = 0;
    mode = "speak";
    setStatus("Connecting…", "info");
    try {
      await ensureWs();
    } catch {
      return false;
    }
    send({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: text }],
      },
    });
    send({
      type: "response.create",
      response: { modalities: ["audio"], instructions: opts.instructions || null },
    });
    return true;
  }

  function speakLatest() {
    const ul = document.querySelector("#voice-panel ul[data-latest-text]");
    if (!ul) return false;
    const text = ul.getAttribute("data-latest-text");
    if (!text) return false;
    return speak(text);
  }

  async function startMic() {
    await loadSession();
    if (!session.enabled) {
      setStatus("Talk to agent requires AZURE_VOICE_LIVE_ENDPOINT", "warn");
      return;
    }
    if (!session.duplex_enabled) {
      setStatus("Talk to agent requires AZURE_VOICE_LIVE_AGENT_ID", "warn");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: SAMPLE_RATE } });
    } catch (e) {
      setStatus("Microphone denied", "error");
      return;
    }
    mode = "mic";
    setStatus("Connecting mic…", "info");
    try { await ensureWs(); } catch { return; }
    const ctx = getCtx();
    const source = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => {
      if (mode !== "mic") return;
      const input = e.inputBuffer.getChannelData(0);
      // Resample if AudioContext is not at SAMPLE_RATE.
      const data = ctx.sampleRate === SAMPLE_RATE ? input : downsample(input, ctx.sampleRate, SAMPLE_RATE);
      const pcm = float32ToPcm16(data);
      send({ type: "input_audio_buffer.append", audio: bytesToBase64(pcm) });
    };
    source.connect(proc);
    proc.connect(ctx.destination);  // Required to keep node alive in some browsers.
    mic = { stream, source, processor: proc };
    micActive = true;
    updateMicButton(true);
  }

  function downsample(buffer, fromRate, toRate) {
    if (toRate === fromRate) return buffer;
    const ratio = fromRate / toRate;
    const newLen = Math.floor(buffer.length / ratio);
    const out = new Float32Array(newLen);
    for (let i = 0; i < newLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.floor((i + 1) * ratio);
      let sum = 0;
      for (let j = start; j < end; j++) sum += buffer[j];
      out[i] = sum / Math.max(1, end - start);
    }
    return out;
  }

  function stopMic() {
    if (mic) {
      try { mic.processor.disconnect(); } catch {}
      try { mic.source.disconnect(); } catch {}
      try { mic.stream.getTracks().forEach((t) => t.stop()); } catch {}
      mic = null;
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      send({ type: "input_audio_buffer.commit" });
      try { ws.close(); } catch {}
    }
    mode = "idle";
    micActive = false;
    setStatus("Idle", "info");
    updateMicButton(false);
  }

  function toggleMic() {
    if (micActive) stopMic(); else startMic();
  }

  function updateMicButton(active) {
    const btn = document.getElementById("voice-mic-btn");
    if (!btn) return;
    btn.textContent = active ? "🛑 Stop talking" : "🎙️ Talk to agent";
    btn.classList.toggle("ring-2", active);
    btn.classList.toggle("ring-rose-400", active);
  }

  // Public API.
  window.voiceLive = { speak, speakLatest, startMic, stopMic, toggleMic, loadSession };

  // After the voice partial swaps in (i.e. after "Speak status"), play the
  // newest utterance through Voice Live if configured.
  document.addEventListener("htmx:afterSwap", (evt) => {
    if (!evt.target || evt.target.id !== "voice-panel") return;
    if (!evt.detail || !evt.detail.requestConfig) return;
    const cfg = evt.detail.requestConfig;
    if (cfg.verb !== "post" || cfg.path !== "/actions/voice") return;
    speakLatest();
  });
})();
