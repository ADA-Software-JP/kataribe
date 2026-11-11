import { S, api, emit } from "./state.js";
import { byId, lines } from "./live.js";
import { isActive } from "./tab.js";

const BIN = 0.02;
let ctx = null;
let master = null;
let volume = readVolume();
let current = null;
let next = null;
let last = null;
let timer = 0;
const FADE = 0.06;          // 声を切る時のフェード(秒)
const loaded = new Map();
const failed = new Map();   // 行 id -> 読み込みに失敗した回数

function readVolume() {
  try { return Number(localStorage.getItem("irodori.volume") ?? 80) / 100; } catch { return 0.8; }
}

export const getVolume = () => volume;

/** 音の出口は読み込み時に作る(再読み込みの直後でも、続きの声がすぐ鳴るように)。ブラウザが許さない間は止まったままで、最初の操作で動かす。 */
export function initAudio() {
  ctx = new AudioContext();
  master = ctx.createGain();
  master.gain.value = volume;
  master.connect(ctx.destination);
  ctx.addEventListener("statechange", () => { pump(); arm(); emit("audio"); });
  if (S.live.running && isActive()) ctx.resume().catch(() => {});
}

/** 動かしたいのに、ブラウザの制限で音が止まっている。 */
let resumeAsked = 0;   // 再開を頼んだ時刻。ブラウザが動かし直すまでの数 ms は「止められている」とは言わない
export const blocked = () => Boolean(ctx) && isActive() && ctx.state === "suspended" && S.live.running && Date.now() - resumeAsked > 300;

function askResume() {
  resumeAsked = Date.now();
  setTimeout(() => emit("audio"), 350);
  return ctx.resume();
}

export function unlock() {
  if (!isActive()) return;
  if (S.live.running && ctx.state === "suspended") askResume().then(pump).catch(() => {});
  else pump();
}

/** 読み込み直した時に、前のページが鳴らしかけたまま終わりを伝えなかった声を「鳴った」に直す。 */
export function settle() {
  if (!isActive()) return;
  for (const l of lines()) {
    if (l.state === "playing" && l.id !== current?.id) {
      l.state = "played";
      api("POST", "/api/live/played", { id: l.id, done: true }).catch(() => {});
      emit("player-end", l);
    }
  }
}

export function setVolume(v) {
  volume = v;
  if (master) master.gain.value = v;
  try { localStorage.setItem("irodori.volume", String(Math.round(v * 100))); } catch { /* 保存できなくても動く */ }
}

export function setRunning(running) {
  if (!ctx) return;
  if (running && isActive() && ctx.state === "suspended") askResume().then(pump).catch(() => {});
  else if (!running && ctx.state === "running") ctx.suspend().then(arm);
}

function envelope(buf) {
  const data = buf.getChannelData(0);
  const size = Math.max(1, Math.round(buf.sampleRate * BIN));
  const env = new Float32Array(Math.ceil(data.length / size));
  let peak = 0.0001;
  for (let i = 0; i < env.length; i++) {
    let m = 0;
    const end = Math.min(data.length, (i + 1) * size);
    for (let j = i * size; j < end; j++) {
      const v = Math.abs(data[j]);
      if (v > m) m = v;
    }
    env[i] = m;
    if (m > peak) peak = m;
  }
  for (let i = 0; i < env.length; i++) env[i] /= peak;
  return env;
}

function ensureLoaded(line) {
  if (loaded.has(line.id)) return;
  const entry = { buf: null, env: null };
  loaded.set(line.id, entry);
  fetch(`/api/live/audio/${line.id}.wav`)
    .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.status))))
    .then((data) => ctx.decodeAudioData(data))
    .then((buf) => { entry.buf = buf; entry.env = envelope(buf); pump(); })
    .catch(() => {
      // 取得・デコードに失敗した: 少し待ってから読み直す(3 回まで)。それでも駄目なら、その行は飛ばして次へ進む
      loaded.delete(line.id);
      const tries = (failed.get(line.id) || 0) + 1;
      failed.set(line.id, tries);
      if (tries < 3) setTimeout(pump, 1000 * tries);
      else {
        line.state = "failed";
        failed.delete(line.id);   // サーバーが 1 度だけ作り直すので、その時はまた 3 回まで読み直す
        api("POST", "/api/live/played", { id: line.id, failed: true }).catch(() => {});
        pump();
      }
    });
}

/** buf を at から鳴らす。切る時にフェードできるよう、1 本ごとに音量の節を持つ。 */
function play(buf, at) {
  const gain = ctx.createGain();
  gain.connect(master);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(gain);
  src.start(at);
  return { src, gain };
}

/** 鳴っている 1 本を at までにフェードして止める。 */
function fadeOut(item, at) {
  const from = Math.max(ctx.currentTime, at - FADE);
  item.gain.gain.cancelScheduledValues(from);
  item.gain.gain.setValueAtTime(item.gain.gain.value, from);
  item.gain.gain.linearRampToValueAtTime(0, at);
  try { item.src.stop(at + 0.01); } catch { /* 終了済み */ }
}

function schedule(line, at) {
  const { buf, env } = loaded.get(line.id);
  next = { id: line.id, ...play(buf, at), startAt: at, endAt: at + buf.duration, buf, env, line };
  arm();
}

export function pump() {
  if (!ctx || !isActive()) return;
  const ready = lines().filter((l) => l.state === "ready");
  ready.slice(0, S.config.tuning?.preload ?? 3).forEach(ensureLoaded);
  if (!S.live.running || ctx.state !== "running" || next) return;
  const first = ready[0];
  if (!first || !loaded.get(first.id)?.buf) return;
  const now = ctx.currentTime;
  if (first.from && current && now < current.endAt - 0.15) {
    // 途中の一言への返事ができた: 今の声を言い終わるまで待たず、すぐ切って差し替える
    const at = now + FADE + 0.02;
    fadeOut(current, at - 0.02);
    current.endAt = at - 0.02;
    current.cut = true;
    schedule(first, at);
    return;
  }
  const base = current || last;
  schedule(first, base ? Math.max(base.endAt + (base.line.pause ?? 0), now + 0.02) : now + 0.04);
}

function finish(item) {
  item.line.state = "played";
  api("POST", "/api/live/played", { id: item.id, done: true, cut: Boolean(item.cut) }).catch(() => {});
  emit("player-end", item.line);
  for (const id of [...loaded.keys()]) {
    const l = byId(id);
    if (!l || ["played", "dropped"].includes(l.state)) loaded.delete(id);
  }
}

function tick() {
  if (!ctx || ctx.state !== "running") return;
  const now = ctx.currentTime;
  if (current && now >= current.endAt) {
    finish(current);
    last = current;
    current = null;
  }
  if (next && now >= next.startAt) {
    current = next;
    next = null;
    current.line.state = "playing";
    api("POST", "/api/live/played", { id: current.id }).catch(() => {});
    emit("player-start", current.line);
  }
  if (!next) pump();
  arm();
}

/** 次に切り替わる時刻にだけ起きる。何も鳴っていない間は何も回さない。 */
function arm() {
  clearTimeout(timer);
  if (!ctx || ctx.state !== "running") return;
  const at = [current?.endAt, next?.startAt].filter((t) => t !== undefined);
  if (!at.length) return;
  timer = setTimeout(tick, Math.min(250, Math.max(10, (Math.min(...at) - ctx.currentTime) * 1000)));
}

export function onDrop(ids) {
  if (next && ids.includes(next.id)) {
    next.src.stop();
    next = null;
  }
  for (const id of ids) loaded.delete(id);
  pump();
}

export function onReset() {
  for (const item of [current, next]) {
    if (item) { try { item.src.stop(); } catch { /* 終了済み */ } }
  }
  current = next = last = null;
  clearTimeout(timer);
  loaded.clear();
}

/** 担当を別のタブに譲る。鳴りかけの声は終わったことにして、音を止める。 */
export function release() {
  if (!ctx) return;
  if (current) finish(current);
  onReset();
  if (ctx.state === "running") ctx.suspend();
}

export function position() {
  if (!ctx || !current) return null;
  const t = Math.min(Math.max(ctx.currentTime - current.startAt, 0), current.buf.duration);
  return { id: current.id, t, dur: current.buf.duration, env: current.env, bin: BIN };
}
