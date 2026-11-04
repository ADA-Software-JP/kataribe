import { needsLogin } from "./login.js";
import { t } from "./i18n.js";
import { iconEl } from "./icons.js";
import { hasContent, lines } from "./live.js";
import { position } from "./player.js";
import { isActive } from "./tab.js";
import { $, S, h, on, setText } from "./state.js";

let shownKey = null;
let waitRef = null;
let accent = "#ededed";
const flowEls = new Map();
const FLOW_KEEP = 4;
const FLOW_GAP = 14;
const FLOW_SCALE = 0.6;
const LONG_LINE = 26;

function readColors() {
  accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
}

function lastUser() {
  return [...S.live.log].reverse().find((e) => e.kind === "user")?.text || "";
}

function swapNow(key, build) {
  if (shownKey === key) return;
  // 古い文を残すと新しい文と重なって二重に見えるので、すぐ入れ替えて新しい方だけ薄く現す
  const next = build();
  $("#nowLine").replaceChildren(next);
  next.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
  shownKey = key;
}

/** 声が鳴り始める前に、何を待っているか。 */
function waitModel() {
  const e = S.engine;
  if (e.state === "starting") return { key: "wait:engine", title: t("wait.engine"), note: "", bar: true };
  if (e.state === "warming") return { key: "wait:warm", title: t("wait.warm"), note: "", bar: true };
  if (S.live.agent.state === "waking") return { key: "wait:agent", title: t("wait.agent"), note: "", bar: true };
  const asked = lastUser();
  const quoted = asked ? t("wait.quoted", { text: asked.length > 24 ? `${asked.slice(0, 24)}…` : asked }) : t("wait.this");
  if (needsLogin()) return { key: "wait:login", bar: false, note: t("wait.login_note"), title: t("wait.login_title", { quoted, name: S.llm.auth.name }) };
  if (S.live.agent.error) return { key: "wait:failed-llm", bar: false, note: "", title: t("wait.failed_llm", { quoted }) };
  if (S.live.tts_error) return { key: "wait:failed", bar: false, note: "", title: t("wait.failed_tts", { quoted }) };
  return { key: "wait:ask", bar: true, note: "", title: asked ? t("wait.making", { quoted }) : t("wait.idle") };
}

function buildWait(m) {
  const title = h("span", {}, m.title);
  const note = h("div", { class: "wait-note" }, m.note);
  waitRef = { title, note };
  return h("div", { class: "now-text waiting" }, h("div", { class: "wait-title" }, m.bar ? iconEl("loader-circle", "spin") : null, title), note);
}

/**
 * 鳴った行を歌詞のように積む。今の行は大きく明るく、前の 2 行は小さく暗く、それより前は消える。
 * 行の大きさは変えず、位置と縮小(transform)だけを動かすので、文字が組み直されず跳ねない。
 */
function renderFlow(items) {
  let flow = $("#nowLine .flow");
  if (!flow) {
    $("#nowLine").replaceChildren();
    waitRef = null;
    flowEls.clear();
    flow = h("div", { class: "flow" });
    $("#nowLine").append(flow);
  }
  shownKey = "flow";
  const keep = new Set(items.map((l) => l.id));
  for (const [id, el] of flowEls) {
    if (!keep.has(id)) { flowEls.delete(id); el.remove(); }
  }
  const fresh = [];
  for (const l of items) {
    if (flowEls.has(l.id)) continue;
    const el = h("div", { class: `flow-line${[...l.text].length > LONG_LINE ? " long" : ""}`, dataset: { rank: "enter" } }, l.text);
    flowEls.set(l.id, el);
    flow.append(el);
    fresh.push(el);
  }
  if (fresh.length) flow.getBoundingClientRect();
  let offset = 0;
  [...items].reverse().forEach((l, rank) => {
    const el = flowEls.get(l.id);
    el.dataset.rank = String(rank);
    el.style.setProperty("--y", `${-offset}px`);
    offset += el.offsetHeight * (rank === 0 ? 1 : FLOW_SCALE) + FLOW_GAP;
  });
}

function renderNow() {
  const content = hasContent();
  if (!content) {
    shownKey = null;
    waitRef = null;
    flowEls.clear();
    $("#nowLine").replaceChildren();
    return;
  }
  const spoken = lines().filter((l) => l.state === "playing" || l.state === "played").slice(-FLOW_KEEP);
  if (spoken.length) {
    renderFlow(spoken);
  } else if (shownKey === null || shownKey.startsWith("wait")) {
    const m = waitModel();
    if (shownKey === m.key && waitRef) {
      setText(waitRef.title, m.title);
      setText(waitRef.note, m.note);
    } else {
      swapNow(m.key, () => buildWait(m));
    }
  }
}

/* 波: 細い数本の線が重なり、今の振幅で揺れる。両端は消える。声が鳴っている間と、鎮まるまでの間だけ描く。 */
const FRAME_MS = 33;
const STEP = 5;
const RIBBONS = [
  { f: 1.7, g: 3.1, p: 0, q: 0.6, s: 1, w: 2, a: 1 },
  { f: 1.7, g: 3.1, p: 0.5, q: 1.2, s: 0.9, w: 1, a: 0.55 },
  { f: 1.7, g: 2.7, p: 1.0, q: 2.4, s: 0.78, w: 1, a: 0.4 },
  { f: 2.1, g: 3.4, p: 1.6, q: 0.2, s: 0.66, w: 1, a: 0.32 },
  { f: 1.4, g: 2.9, p: 2.2, q: 1.8, s: 0.52, w: 1, a: 0.26 },
  { f: 2.4, g: 3.8, p: 2.8, q: 3.0, s: 0.4, w: 1, a: 0.2 },
];
let level = 0;
let raf = 0;
let lastDraw = 0;
let canvas = null;
let ctx2d = null;

/** 今の振幅(直近 60ms の最大)。鳴っている間は少し底上げして線が揺れ続ける。鳴っていなければ 0。 */
function levelNow() {
  const pos = position();
  if (!pos) return 0;
  const a = Math.max(0, Math.floor((pos.t - 0.06) / pos.bin));
  let amp = 0;
  for (let i = a; i <= Math.floor(pos.t / pos.bin) && i < pos.env.length; i++) amp = Math.max(amp, pos.env[i]);
  return 0.2 + 0.8 * Math.pow(amp, 0.7);
}

// 止まっている時も、波は静かな線で残す(文字と操作の間が空かないように)
const IDLE = 0.12;

function drawWave(now) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const hgt = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hgt * dpr);
  }
  ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx2d.clearRect(0, 0, w, hgt);
  level += (levelNow() - level) * 0.22;
  const t = now / 1000;
  const mid = hgt / 2;
  const amp = (hgt / 2 - 6) * Math.max(level, IDLE);
  const grad = ctx2d.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, "transparent");
  grad.addColorStop(0.5, accent);
  grad.addColorStop(1, "transparent");
  ctx2d.strokeStyle = grad;
  ctx2d.lineJoin = "round";
  ctx2d.shadowColor = accent;
  for (const r of RIBBONS) {
    ctx2d.beginPath();
    for (let x = 0; x <= w; x += STEP) {
      const u = x / w;
      const edge = Math.pow(Math.sin(Math.PI * u), 1.6);
      const y = mid + amp * r.s * edge * (0.62 * Math.sin(u * Math.PI * 2 * r.f + t * 1.1 + r.p) + 0.38 * Math.sin(u * Math.PI * 2 * r.g - t * 1.7 + r.q));
      if (x === 0) ctx2d.moveTo(x, y);
      else ctx2d.lineTo(x, y);
    }
    ctx2d.globalAlpha = r.a;
    ctx2d.lineWidth = r.w;
    ctx2d.shadowBlur = r.w > 1 ? 14 : 0;
    ctx2d.stroke();
  }
  ctx2d.globalAlpha = 1;
}

const wantAnimate = () => !document.hidden && isActive() && S.live.running && (Boolean(position()) || level > 0);

function frame(now) {
  raf = 0;
  if (now - lastDraw >= FRAME_MS) {
    lastDraw = now;
    drawWave(now);
  }
  if (wantAnimate()) raf = requestAnimationFrame(frame);
  else kick();
}

/** 動かす必要が出たら 1 本だけループを始め、要らなくなったら止めて、波を静かな線にする。 */
function kick() {
  if (wantAnimate()) {
    if (!raf) raf = requestAnimationFrame(frame);
  } else {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    level = 0;
    drawWave(lastDraw);
  }
}

export function initStage() {
  readColors();
  canvas = $("#wave");
  ctx2d = canvas.getContext("2d");
  on("theme", readColors);
  const all = () => { renderNow(); kick(); };
  for (const ev of ["live", "live-entry", "live-line", "player-start", "player-end", "llm", "engine", "tab"]) on(ev, all);
  on("tick", renderNow);
  document.addEventListener("visibilitychange", kick);
  window.addEventListener("resize", () => { if (!raf) drawWave(lastDraw); });
  on("live-reset", () => { shownKey = null; level = 0; all(); });
  all();
}
