import { needsLogin } from "./login.js";
import { initComposer, insertNewline, micCancel, micCommit, micRecording, micStart, submit, syncInput } from "./composer.js";
import { askRestart, initControls, openVoicePick, renderControls as renderButtons } from "./controls.js";
import { hydrate } from "./icons.js";
import { applyStatic, loadDicts, setLang, t } from "./i18n.js";
import { addEntry, applySnapshot, applyStatus, clearLog, dropLines, hasContent, patchLine } from "./live.js";
import { initAudio, onDrop, onReset, pump, release, setRunning, settle, unlock } from "./player.js";
import { applyTheme, capturingKey, openHelp, openSettings, requestCloseSettings, settingsOpen, themePref } from "./settings.js";
import { actionsFor, comboOf, isPlainPrintable, loadShortcuts } from "./shortcuts.js";
import { initSetup } from "./setup.js";
import { initHistory } from "./history.js";
import { initStage } from "./stage.js";
import { alone, claim, initTab, isActive, onClaim } from "./tab.js";
import { $, S, api, emit, on, toast } from "./state.js";

/* 画面の段階: 初回(入力欄だけ)と会話中 --------------------------------------- */
let phase = "first";

/** 入力欄が初回の中央から下へ滑らかに移る(0.4 秒)。 */
function setPhase(next, animate) {
  if (next === phase) return;
  const dock = $("#dock");
  const before = dock.getBoundingClientRect().top;
  phase = next;
  $("#app").dataset.phase = next;
  syncInput();
  const dy = before - dock.getBoundingClientRect().top;
  if (!animate || !dy) return;
  dock.style.transition = "none";
  dock.style.transform = `translateY(${dy}px)`;
  dock.getBoundingClientRect();
  dock.style.transition = "transform 0.4s var(--ease)";
  dock.style.transform = "";
}

const syncPhase = (animate = true) => setPhase(hasContent() ? "live" : "first", animate);

/* ヘッダー ------------------------------------------------------------ */
/** 上部の点は、エンジンとエージェントの状態を 1 つにまとめる。くわしい文はマウスを載せると出る。 */
function renderEngine() {
  const e = S.engine;
  const l = S.llm;
  const pill = $("#enginePill");
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - e.since));
  let state = "ready";
  let text = t("engine.ready");
  if (e.state === "error") {
    state = "error";
    text = t("engine.error", { error: e.error });
  } else if (e.state === "stopped") {
    state = "stopped";
    text = t("engine.stopped");
  } else if (e.state === "starting") {
    state = "busy";
    text = t("engine.starting", { secs });
  } else if (e.state === "warming") {
    state = "busy";
    text = t("engine.warming", { secs });
  } else if (needsLogin()) {
    state = S.live.agent.error ? "error" : "warn";
    text = l.auth.state === "login" ? t("engine.login_wait") : t("engine.login_out", { name: l.auth.name });
  } else if (S.live.agent.error) {
    state = "error";
    text = t("engine.llm_error");
  } else if (!l.ok) {
    state = "warn";
    text = t("engine.ready_no_llm");
  } else if (l.state === "waking") {
    state = "busy";
    text = t("engine.ready_llm_waking");
  }
  if (pill.dataset.state !== state) pill.dataset.state = state;
  const tip = `${text}\n${t("engine.llm_line", { label: l.label })}`;
  if (pill.title !== tip) pill.title = tip;
  if (pill.getAttribute("aria-label") !== text) pill.setAttribute("aria-label", text);
}

function renderControls() {
  $("#otherTab").classList.toggle("hidden", isActive() || !hasContent());
  renderEngine();
  renderButtons();
}

async function toggleRunning() {
  const next = !S.live.running;
  S.live.running = next;
  setRunning(next);
  renderControls();
  emit("live");
  try { await api("POST", next ? "/api/live/resume" : "/api/live/stop", {}); } catch (e) { toast(e.message, { kind: "err" }); }
}

function initHeader() {
  on("toggle-running", () => { unlock(); toggleRunning(); });
  $("#settingsBtn").addEventListener("click", () => openSettings());
  $("#scrim").addEventListener("click", requestCloseSettings);
  $("#enginePill").addEventListener("click", async () => {
    if (S.engine.state === "error" || S.live.agent.error || needsLogin()) openSettings(S.engine.state === "error" ? undefined : "agent");
    else if (S.engine.state === "stopped") await api("POST", "/api/engine/start", {});
  });
  $("#takeBtn").addEventListener("click", claim);
}

/** 声を鳴らす担当は 1 タブだけ。譲る時は鳴りかけの声を止め、受ける時は(押した操作のうちに)鳴らし始める。 */
function syncTab() {
  if (isActive()) unlock();
  else release();
  renderControls();
}

/* SSE ----------------------------------------------------------------- */
function connect() {
  const es = new EventSource("/api/events");
  const on_ = (name, fn) => es.addEventListener(name, (ev) => fn(JSON.parse(ev.data)));
  on_("snapshot", (d) => {
    Object.assign(S, { engine: d.engine, setup: d.setup, llm: d.llm, voices: d.voices, config: d.config });
    emit("setup");
    applySnapshot(d.live);
    settle();
    renderControls();
  });
  on_("setup", (d) => { S.setup = d; emit("setup"); });
  on_("engine", (d) => { S.engine = d; renderControls(); emit("engine"); api("GET", "/api/voices").then((r) => { S.voices = r.voices; }).catch(() => {}); });
  on_("llm", (d) => { S.llm = d; renderControls(); emit("llm"); });
  on_("live_status", (d) => { applyStatus(d); setRunning(d.running); renderControls(); });
  on_("live_entry", addEntry);
  on_("live_line", patchLine);
  on_("live_drop", (d) => { dropLines(d.ids); onDrop(d.ids); renderControls(); });
  on_("live_reset", () => { onReset(); clearLog(); renderControls(); });
  on_("live_load", (d) => { onReset(); applySnapshot(d); settle(); renderControls(); });
  on_("history", () => emit("history"));
  on_("live_notice", (d) => toast(d.text, { kind: d.kind }));
  on_("player", (d) => onClaim(d.id));
  on("live-line", () => { pump(); renderControls(); });
}

/* キーボード ---------------------------------------------------------- */
function inTextField(t) {
  return t instanceof HTMLElement && (t.matches("textarea, input:not([type=range]):not([type=checkbox]), select") || t.isContentEditable);
}

/** 一時停止(Esc): 設定を開いている時は閉じ、それ以外は止める。 */
function pauseAction() {
  if (settingsOpen()) requestCloseSettings();
  else if (S.live.running && S.live.log.length) toggleRunning();
}

// 操作ごとの動き。キーとの対応は設定の shortcuts(shortcuts.js)。target は押された場所
const KEY_ACTIONS = {
  send: (target) => { if (target !== $("#input")) return false; submit(); },
  newline: (target) => { if (target !== $("#input")) return false; insertNewline(); },
  pause: pauseAction,
  toggle: toggleRunning,
  help: openHelp,
  restart: askRestart,
  voice: openVoicePick,
  settings: () => (settingsOpen() ? requestCloseSettings() : openSettings()),
  mic_start: () => { if (!micRecording()) micStart(); },
  mic_commit: () => { if (micRecording()) micCommit(); else return false; },
  mic_cancel: () => { if (micRecording()) micCancel(); else return false; },
};

function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (capturingKey() || document.querySelector("dialog[open]") || e.isComposing) return;
    const combo = comboOf(e);
    const typing = inTextField(e.target) && isPlainPrintable(combo);
    // 録音中の確定・取り消しは、同じキーの別の操作(Esc の一時停止など)より先に働く
    const recFirst = (id) => (micRecording() && id.startsWith("mic_c") ? 0 : 1);
    const ids = actionsFor(combo).sort((a, b) => recFirst(a) - recFirst(b));
    for (const id of typing ? [] : ids) {
      if (KEY_ACTIONS[id](e.target) === false) continue;
      e.preventDefault();
      return;
    }
    if (!inTextField(e.target) && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.length === 1 && !settingsOpen()) $("#input").focus();
  });
  document.addEventListener("pointerdown", unlock);
  document.addEventListener("keydown", unlock);
}

/* 起動 ---------------------------------------------------------------- */
initTab();
await loadDicts();
let first = await api("GET", "/api/state");
setLang(first.lang);
applyStatic();
// 前に開いていた画面の続きは、開いただけで鳴らさない。会話は消さずに止めて残す(▶ で続きから)。ほかのタブで聞いている時はそのまま
if (first.live.log.length && first.live.running && await alone()) {
  await api("POST", "/api/live/stop", {});
  first = await api("GET", "/api/state");
}
Object.assign(S, { engine: first.engine, setup: first.setup, llm: first.llm, voices: first.voices, config: first.config, platform: first.platform, osLang: first.os_lang,
  promptDefaults: first.prompt_defaults, promptVars: first.prompt_vars, promptLimits: first.prompt_limits,
  modelChoices: first.model_choices, defaultModels: first.default_models, efforts: first.efforts,
  tuningSpec: first.tuning_spec });
S.live = first.live;
hydrate();
initAudio();
on("lang", () => { applyStatic(); renderControls(); syncInput(); for (const ev of ["live", "llm", "engine", "voices"]) emit(ev); });
settle();
applyTheme(themePref());
syncPhase(false);
on("live", syncPhase);
on("live-entry", syncPhase);
on("live-reset", syncPhase);
loadShortcuts(S.config.shortcuts);
initStage();
initHistory();
initComposer();
initControls();
initSetup();
initHeader();
renderControls();
connect();
initKeys();
on("open-settings", openSettings);
on("config-changed", () => emit("llm"));
on("tab", syncTab);
on("live", renderControls);
on("player-start", renderControls);
on("player-end", renderControls);
setInterval(() => { if (["starting", "warming"].includes(S.engine.state)) { renderEngine(); emit("tick"); } }, 1000);
document.addEventListener("visibilitychange", () => { document.documentElement.dataset.hidden = String(document.hidden); });
// 状態を読み込む前の配置(例のピルが出る前の、入力欄が中央にある位置)は見せない。読み込めてから出す
$("#app").dataset.ready = "1";
$("#input").focus();
