const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function client(enabled = true) {
  const sources = [];
  const sockets = [];
  const status = { dataset: {}, textContent: "" };
  const track = { stopped: false, stop() { this.stopped = true; } };
  const speech = { cancellations: 0, cancel() { this.cancellations++; }, getVoices() { return []; }, speak() {} };
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor() {
      this.readyState = 1;
      this.sent = [];
      sockets.push(this);
      setImmediate(() => this.event({ type: "session.updated" }));
    }
    event(event) { this.onmessage({ data: JSON.stringify(event) }); }
    send(text) { this.sent.push(JSON.parse(text)); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  class AudioContext {
    currentTime = 0;
    sampleRate = 24000;
    state = "running";
    destination = {};
    createBuffer() { return { duration: 1, copyToChannel() {} }; }
    createBufferSource() {
      const source = {
        stopped: false, connect() {}, disconnect() {}, start() {},
        stop() { this.stopped = true; },
      };
      sources.push(source);
      return source;
    }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createScriptProcessor() { return { connect() {}, disconnect() {} }; }
  }
  const window = { AudioContext, speechSynthesis: speech };
  const context = {
    window, console, WebSocket: Socket, setTimeout, clearTimeout,
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) } },
    location: { protocol: "https:", host: "test.local" },
    document: { addEventListener() {}, getElementById: id => id === "voice-status" ? status : null },
    fetch: async () => ({ json: async () => ({ enabled, duplex_enabled: enabled, ws_path: "/ws/voice" }) }),
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    atob: text => Buffer.from(text, "base64").toString("binary"),
    btoa: text => Buffer.from(text, "binary").toString("base64"),
  };
  vm.runInNewContext(fs.readFileSync(
    path.join(__dirname, "..", "src", "fibreops", "ui", "static", "voice-live.js"), "utf8",
  ), context);
  return { api: window.voiceAgent, sources, sockets, track, speech, status };
}

function audio(socket, id) {
  socket.event({ type: "response.output_audio.delta", response_id: id, delta: "AQABAA==" });
}

test("stop cancels generation and queued audio, keeps mic open, and allows the next answer", async () => {
  const c = client();
  await c.api.startMic();
  const ws = c.sockets[0];
  ws.event({ type: "response.created", response: { id: "r1" } });
  audio(ws, "r1");
  audio(ws, "r1");
  c.api.stopResponse();
  assert.ok(c.sources.every(s => s.stopped));
  assert.deepEqual(ws.sent.at(-1), { type: "response.cancel", response_id: "r1" });
  assert.equal(ws.readyState, 1);
  assert.equal(c.track.stopped, false);
  audio(ws, "r1");
  assert.equal(c.sources.length, 2);
  ws.event({ type: "response.done", response: { id: "r1", status: "cancelled" } });
  ws.event({ type: "input_audio_buffer.speech_started" });
  ws.event({ type: "response.created", response: { id: "r2" } });
  audio(ws, "r2");
  assert.equal(c.sources.length, 3);
  assert.equal(c.sources[2].stopped, false);
  c.api.stopMic();
});

test("barge-in stops old queued audio and suppresses its late chunks", async () => {
  const c = client();
  await c.api.startMic();
  const ws = c.sockets[0];
  ws.event({ type: "response.created", response: { id: "old" } });
  audio(ws, "old");
  ws.event({ type: "input_audio_buffer.speech_started" });
  audio(ws, "old");
  assert.equal(c.sources.length, 1);
  assert.equal(c.sources[0].stopped, true);
  c.api.stopMic();
});

test("stop between speech and generation cancels the pending response", async () => {
  const c = client();
  await c.api.startMic();
  const ws = c.sockets[0];
  c.api.stopResponse();
  ws.event({ type: "response.created", response: { id: "pending" } });
  audio(ws, "pending");
  assert.equal(ws.sent.at(-1).type, "response.cancel");
  assert.equal(c.sources.length, 0);
  c.api.stopMic();
});

test("stop clears buffered playback even after server generation completed", async () => {
  const c = client();
  await c.api.startMic();
  const ws = c.sockets[0];
  ws.event({ type: "response.created", response: { id: "done" } });
  audio(ws, "done");
  ws.event({ type: "response.done", response: { id: "done", status: "completed" } });
  c.api.stopResponse();
  assert.equal(c.sources[0].stopped, true);
  assert.equal(ws.sent.length, 0);
  c.api.stopMic();
});

test("stop cancels offline browser speech and a pending announcement", async () => {
  const c = client(false);
  await c.api.speak("An English status update.");
  c.api.stopResponse();
  assert.equal(c.speech.cancellations, 2);
  assert.equal(c.status.textContent, "Response stopped");
  const live = client();
  const pending = live.api.speak("Do not play this.");
  live.api.stopResponse();
  assert.equal(await pending, false);
  assert.equal(live.sockets.length, 0);
});

test("announcements carry structured facts without converting unknowns to zero", async () => {
  const c = client();
  await c.api.speak("The restoration time is unknown.", {
    potentially_affected_customers: 800,
    service_restoration_eta_minutes: null,
  });
  const message = c.sockets[0].sent.find(e => e.type === "conversation.item.create");
  const text = message.item.content[0].text;
  assert.ok(text.includes('"potentially_affected_customers":800'));
  assert.ok(text.includes('"service_restoration_eta_minutes":null'));
  c.api.stopResponse();
});
