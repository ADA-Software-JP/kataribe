import { onInputChange } from "./controls.js";
import { authOf, needsLogin, startLogin } from "./login.js";
import { svg } from "./icons.js";
import { getLang, t } from "./i18n.js";
import { hasContent } from "./live.js";
import { crossfade } from "./motion.js";
import { blocked, unlock } from "./player.js";
import { keyOf, showKey } from "./shortcuts.js";
import { $, S, api, emit, h, on, toast } from "./state.js";

const input = () => $("#input");

/** 最初の画面の例。押すと入力欄に入るだけで、送るのは Enter か送信ボタン。LLM が使えない時は出さない(setup.js のカードが出る)。 */
const PRESETS = ["fairy", "asmr", "situation", "sleep", "lecture"];

function renderPresets() {
  const box = $("#presets");
  const show = S.llm.ok && !hasContent();
  box.classList.toggle("hidden", !show);
  if (box.children.length && box.dataset.lang === getLang()) return;
  box.dataset.lang = getLang();
  box.replaceChildren(...PRESETS.map((id) => h("button", { type: "button", class: "preset", onclick: () => {
    const el = input();
    const text = t(`preset.${id}.text`);
    el.value = text;
    autosize();
    el.focus();
    el.setSelectionRange(text.length, text.length);
  } }, t(`preset.${id}.label`))));
}

/** 入力欄の高さを中身に合わせる。空の時は 1 行(案内文の折り返しで伸ばさない)。幅が変わったら測り直す(initComposer)。 */
function autosize() {
  const el = input();
  el.style.height = "auto";
  if (!el.value) return;
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
}

/** 入力欄の文言。大きさは CSS が段階ごとに決める。 */
export function syncInput() {
  const el = input();
  const ph = t(rec ? "r21.listening" : !S.llm.ok ? "ui.placeholder_read" : hasContent() ? "ui.placeholder_live" : "ui.placeholder_first");
  renderPresets();
  if (el.placeholder !== ph) el.placeholder = ph;
  autosize();
}

let sending = false;

export async function submit() {
  const text = input().value.trim();
  if (!text || sending) return;
  sending = true;
  unlock();
  try {
    await api("POST", "/api/live/send", { text });
    if (text === input().value.trim()) input().value = "";
    autosize();
    onInputChange();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    sending = false;
  }
}

const MIN_SHOW = 400;   // 状態の文言を切り替えずに見せておく最短の時間(ms)
const openAgentSettings = () => emit("open-settings", "agent");
const retry = () => api("POST", "/api/live/retry", {}).catch((e) => toast(e.message, { kind: "err" }));
const act = (label, fn, primary = false) => ({ label, fn, primary });
const settings = () => act(t("ui.open_settings"), () => emit("open-settings"));

/** 状態 1 行の中身。モデル名は出さない(ツールチップと設定にだけ)。失敗は理由と直し方を同じ場所に出す。 */
function statusModel() {
  const { agent, running, steer } = S.live;
  const e = S.engine;
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - e.since));
  const out = { msg: "", lead: "", acts: [] };

  if (e.state === "error") return { msg: t("st.engine_error", { error: e.error }), lead: "err", acts: [settings()] };
  if (e.state === "stopped") return { msg: t("st.engine_stopped"), lead: "err", acts: [act(t("st.start"), () => api("POST", "/api/engine/start", {}), true)] };
  if (needsLogin() && hasContent()) {
    const a = authOf();
    return a.state === "login"
      ? { msg: t("st.login_wait", { name: a.name }), lead: "spin", acts: [act(t("ui.open_settings"), () => emit("open-settings", "agent"))] }
      : { msg: t("st.login_out", { name: a.name }), lead: "err", acts: [act(t("st.sign_in"), startLogin, true), settings()] };
  }
  if (agent.error) return { msg: t("st.agent_error", { error: agent.error }), lead: "err", acts: [act(t("st.retry"), retry, true), settings()] };
  if (S.live.tts_error) return { msg: t("st.tts_error", { error: S.live.tts_error }), lead: "err", acts: [act(t("st.retry"), retry, true), settings()] };
  if (e.state === "starting") return { msg: t("st.loading", { secs }), lead: "spin", acts: [] };
  if (e.state === "warming") return { msg: t("st.warming", { secs }), lead: "spin", acts: [] };

  const busy = agent.state === "thinking" || agent.state === "writing";
  // 最初の画面で LLM が使えない時は、用意のしかたを入力欄の上に出している(setup.js)
  if (!hasContent()) return out;
  if (!running) out.msg = t("st.paused");
  else if (blocked()) out.msg = t("st.blocked");
  else if (agent.state === "waking") Object.assign(out, { msg: t("st.llm_waking"), lead: "spin" });
  else if (steer && busy) Object.assign(out, { msg: t("st.steering", { steer }), lead: "spin" });
  else if (agent.state === "searching") Object.assign(out, { msg: t("r21.searching"), lead: "spin" });
  // 作業の途中経過(書いている・声を作っている・読み込んでいる)は出さない
  return out;
}

let applied = { sig: "", at: 0 };
let paceTimer = 0;

/** 部品は作り直さず、文字だけをクロスフェードで差し替える。切り替えは最短 400ms 空ける。 */
export function renderStatus() {
  const m = statusModel();
  const sig = m.msg;
  const wait = applied.at + MIN_SHOW - Date.now();
  clearTimeout(paceTimer);
  if (sig !== applied.sig && applied.sig && wait > 0) {
    paceTimer = setTimeout(renderStatus, wait);
    return;
  }
  if (sig !== applied.sig) applied = { sig, at: Date.now() };
  const el = $("#agentLine");
  const title = t("st.llm_title", { label: S.llm.label });
  if (el.dataset.lead !== m.lead) el.dataset.lead = m.lead;
  const empty = m.msg ? "0" : "1";
  if (el.dataset.empty !== empty) el.dataset.empty = empty;
  el.classList.toggle("err", m.lead === "err");
  const btn = $("#agentBtn");
  if (btn.title !== title) btn.title = title;
  crossfade(btn.querySelector(".xf"), m.msg);
  const acts = $("#agentActs");
  const key = m.acts.map((a) => a.label).join("|");
  if (acts.dataset.key !== key) {
    acts.dataset.key = key;
    acts.replaceChildren(...m.acts.map((a) => h("button", { class: `btn small${a.primary ? " primary" : ""}`, onclick: a.fn }, a.label)));
  }
}

/* 音声入力。ブラウザの音声認識を使う(使えないブラウザではボタンを出さない)。
   聞き取った文は入力欄に入れるだけで、自動では送らない(送るのは Enter か送信ボタン)。
   録音中は「確定」(終えて入力欄に残す)と「取り消し」(録音前の内容に戻す)を出す。話し終わりで認識が止まった時も、文を残して待つ。
   もう一度マイクを押すと、入力欄の今の文の後ろに足す。 */
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;   // 録音中は { r: SpeechRecognition, base: 録音前の入力, lead: 足す時の区切り, done: 確定した文, interim: 途中の文, cancel: 取り消したか }

// 音声認識のエラーごとの表示。理由と直し方を 1 文で。"" は何も出さない（話さなかった、自分で止めた）
const MIC_ERRORS = {
  "not-allowed": "mic.not_allowed",
  "service-not-allowed": "mic.service",
  network: "mic.network",
  "audio-capture": "mic.capture",
  "language-not-supported": "mic.language",
  "no-speech": "",
  aborted: "",
};
const micMessage = (code) => (MIC_ERRORS[code] === "" ? "" : t(MIC_ERRORS[code] ?? "mic.failed", { error: code }));

export const micRecording = () => Boolean(rec);

// 途中の文字は、長ければ終わりの部分だけを見せる(今聞き取っている所が見えるように)
const tail = (text) => (text.length > 48 ? `…${text.slice(-48)}` : text);

const keyText = (id) => showKey(keyOf(id)).join(" + ") || t("r21.sc.unset");

/** マイクの見た目と、録音中の表示(「聞き取り中…」、途中の文字、止め方、枠の色)。 */
function paintMic() {
  const btn = $("#micBtn");
  if (!btn) return;
  const on = Boolean(rec);
  const start = keyOf("mic_start");
  const title = on ? t("r21.mic_stop_title") : `${t("ui.mic")}${start ? ` (${showKey(start).join(" + ")})` : ""}`;
  btn.setAttribute("aria-pressed", String(on));
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.innerHTML = `<i class="i">${svg(on ? "square" : "mic")}</i>`;
  $(".box").dataset.rec = on ? "1" : "";
  $("#rec").classList.toggle("hidden", !on);
  if (on) {
    $("#recHint").textContent = t("r21.rec_hint", { commit: keyText("mic_commit"), cancel: keyText("mic_cancel") });
    $("#recPartial").textContent = tail(rec.interim);
  }
  syncInput();
}

function showTranscript() {
  input().value = rec.base + (rec.done || rec.interim ? rec.lead : "") + rec.done + rec.interim;
  $("#recPartial").textContent = tail(rec.interim);
  autosize();
  onInputChange();
}

function micEnd(state) {
  const input_ = input();
  if (state.cancel) input_.value = state.base;
  else input_.value = state.base + (state.done || state.interim ? state.lead : "") + state.done + state.interim;
  rec = null;
  paintMic();
  autosize();
  onInputChange();
  input_.focus();
  input_.setSelectionRange(input_.value.length, input_.value.length);
}

/** 録音を始める。入力欄に文がある時は、その後ろに足す。 */
export function micStart() {
  if (!Recognition || rec) return;
  const base = input().value;
  const r = new Recognition();
  r.lang = { ja: "ja-JP", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW" }[getLang()] || "en-US";
  r.interimResults = true;
  r.continuous = true;
  const state = { r, base, lead: base && getLang() === "en" && !/\s$/.test(base) ? " " : "", done: "", interim: "", cancel: false };
  r.onresult = (e) => {
    if (rec !== state) return;
    let done = "";
    let interim = "";
    for (const res of e.results) (res.isFinal ? (done += res[0].transcript) : (interim += res[0].transcript));
    state.done = done;
    state.interim = interim;
    showTranscript();
  };
  r.onerror = (e) => {
    const msg = micMessage(e.error);
    if (msg) toast(msg, { kind: "err", ms: 10000 });
  };
  r.onend = () => { if (rec === state) micEnd(state); };
  rec = state;
  try { r.start(); } catch { rec = null; toast(micMessage("service-not-allowed"), { kind: "err", ms: 10000 }); return; }
  paintMic();
}

/** 確定: 聞き取り途中の言葉も拾って止め、入力欄に残す(送らない)。 */
export function micCommit() {
  if (rec) rec.r.stop();
}

/** 取り消し: 録音を捨てて、録音前の入力欄に戻す。 */
export function micCancel() {
  if (!rec) return;
  rec.cancel = true;
  const state = rec;
  state.r.abort();
  // abort で end が来ない認識器でも、画面は必ず戻す
  setTimeout(() => { if (rec === state) micEnd(state); }, 300);
}

function initMic() {
  const btn = $("#micBtn");
  if (!Recognition) { btn.remove(); $("#rec").remove(); return; }
  btn.addEventListener("click", () => (rec ? micCommit() : micStart()));
  $("#recCommit").addEventListener("click", micCommit);
  $("#recCancel").addEventListener("click", micCancel);
  on("shortcuts", paintMic);
  on("lang", paintMic);
  paintMic();
}

export function initComposer() {
  const el = input();
  el.addEventListener("input", () => { autosize(); onInputChange(); });
  let width = 0;
  new ResizeObserver(([entry]) => {
    if (Math.round(entry.contentRect.width) !== width) { width = Math.round(entry.contentRect.width); autosize(); }
  }).observe(el);
  $("#sendBtn").addEventListener("click", submit);
  initMic();
  $("#agentBtn").addEventListener("click", openAgentSettings);
  const refresh = () => { syncInput(); renderStatus(); };
  for (const ev of ["live", "live-entry", "live-line", "player-start", "player-end", "llm", "engine"]) on(ev, ev === "live-line" || ev === "player-start" || ev === "player-end" ? renderStatus : refresh);
  on("tick", renderStatus);
  on("audio", renderStatus);
  on("tab", renderStatus);
  const bind = (id, name) => { const i = $(id); i.innerHTML = svg(name); };
  bind("#agentLine .lead-spin", "loader-circle");
  bind("#agentLine .lead-warn", "triangle-alert");
  refresh();
}

/** 入力欄のカーソルの位置に改行を入れる(「改行」のショートカットが Shift + Enter 以外の時にも使う)。 */
export function insertNewline() {
  const el = input();
  el.setRangeText("\n", el.selectionStart, el.selectionEnd, "end");
  autosize();
  onInputChange();
}
