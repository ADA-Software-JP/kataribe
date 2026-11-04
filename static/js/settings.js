import { iconEl } from "./icons.js";
import { LANGS, getLang, num, setLang, t } from "./i18n.js";
import { loginBlock } from "./login.js";
import { getVolume, setVolume } from "./player.js";
import { toggleStar } from "./live.js";
import { ACTIONS, comboOf, conflictsOf, defaultKey, isModifierOnly, keyOf, labelOf, setKey, showKey } from "./shortcuts.js";
import { $, S, api, emit, h, on, toast } from "./state.js";

const PROVIDERS = [["claude", "Claude Code"], ["codex", "Codex"], ["lmstudio", "LM Studio"]];
const THEMES = [["system", "set.theme.system"], ["dark", "set.theme.dark"], ["light", "set.theme.light"]];
const COLORS = [["mono", "set.color.mono"], ["warm", "set.color.warm"], ["blue", "set.color.blue"], ["green", "set.color.green"], ["pink", "set.color.pink"]];
const SUBS = [["small", "set.subs.small"], ["large", "set.subs.large"], ["off", "set.subs.off"]];
// LLM に渡す文面。名前と説明は辞書の prompt.<キー>.label / .about、既定の文と差し込み名はサーバー(/api/state の prompt_defaults / prompt_vars)から受け取る。
const PROMPTS = ["style", "format", "request", "steer", "handover", "more", "retry", "search", "no_search", "ready", "ping", "ping_system"];

// 「システム」は OS の明るさ設定に合わせ、OS 側で切り替わった時もついていく
const osLight = matchMedia("(prefers-color-scheme: light)");
export function applyTheme(pref) {
  const light = pref === "light" || (pref === "system" && osLight.matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  emit("theme");
}
osLight.addEventListener("change", () => { if (themePref() === "system") applyTheme("system"); });

/** 色と文字の表示。html の data-color / data-subs で CSS が切り替える(初期値は index.html の先頭で入れる)。 */
function setLook(key, value) {
  try { localStorage.setItem(`irodori.${key}`, value); } catch { /* 保存できなくても動く */ }
  document.documentElement.dataset[key] = value;
  emit("theme");
}

export function themePref() {
  try { const v = localStorage.getItem("irodori.theme"); return v === "light" || v === "dark" ? v : "system"; } catch { return "system"; }
}

const keycaps = (combo) => (combo ? showKey(combo).flatMap((p, i) => (i ? [" + ", h("kbd", {}, p)] : [h("kbd", {}, p)])) : [h("span", { class: "sc-unset" }, t("r21.sc.unset"))]);

/** 今のショートカットの一覧(ヘルプ用。割り当てのある操作だけ)。 */
function shortcutList() {
  return h("div", { class: "keys" }, ACTIONS.filter(([id]) => keyOf(id)).flatMap(([id]) => [h("span", {}, keycaps(keyOf(id))), h("span", {}, labelOf(id))]));
}

// キーを待っている間は、画面全体のショートカットを止める(app.js が見る)
let capturing = false;
export const capturingKey = () => capturing;

/** 設定のショートカット欄。欄をクリック → キーを押す → 表示して config.json の shortcuts に保存。重複は警告、項目ごとに既定へ戻せる。 */
function shortcutEditor() {
  const updates = [];
  const save = async (id, combo) => {
    const before = keyOf(id);
    const next = setKey(id, combo);
    emit("shortcuts");
    updates.forEach((u) => u());
    try {
      await api("PUT", "/api/config", { shortcuts: next });
    } catch (e) {
      setKey(id, before);
      emit("shortcuts");
      updates.forEach((u) => u());
      toast(t("set.save_failed", { e: e.message }), { kind: "err" });
    }
  };
  const rowOf = (id) => {
    const name = labelOf(id);
    const field = h("button", { type: "button", class: "sc-key", dataset: { id } });
    const warn = h("div", { class: "sc-warn hidden" });
    const reset = h("button", { type: "button", class: "btn small sc-reset", title: t("set.reset"), "aria-label": t("r21.sc.reset_aria", { name }),
      onclick: () => save(id, defaultKey(id)) }, iconEl("rotate-ccw"));
    let swallow = false;
    const stop = () => { capturing = false; delete field.dataset.listening; update(); };
    const update = () => {
      if (field.dataset.listening) return;
      const key = keyOf(id);
      field.replaceChildren(...keycaps(key));
      field.setAttribute("aria-label", t("r21.sc.field_aria", { name, key: showKey(key).join(" + ") || t("r21.sc.unset") }));
      reset.disabled = key === defaultKey(id);
      const names = conflictsOf(id).map(labelOf);
      warn.classList.toggle("hidden", !names.length);
      warn.textContent = names.length ? t("r21.sc.conflict", { names: names.join(" / ") }) : "";
    };
    field.addEventListener("click", () => {
      if (capturing && field.dataset.listening) return;
      capturing = true;
      field.dataset.listening = "1";
      field.replaceChildren(t("r21.sc.press"));
    });
    field.addEventListener("blur", () => { if (field.dataset.listening) stop(); });
    field.addEventListener("keydown", (e) => {
      if (!field.dataset.listening) return;
      if (e.key === "Tab" && !e.ctrlKey && !e.altKey && !e.metaKey) { stop(); return; }
      e.preventDefault();
      e.stopPropagation();
      if (isModifierOnly(e)) return;
      const bare = !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey;
      const combo = e.key === "Backspace" && bare ? "" : comboOf(e);
      swallow = true;
      stop();
      save(id, combo);
    });
    field.addEventListener("keyup", (e) => { if (swallow) { swallow = false; e.preventDefault(); } });
    updates.push(update);
    update();
    return h("div", { class: "sc-row" }, h("span", { class: "sc-name" }, name), field, reset, warn);
  };
  return h("div", { class: "sc-list" }, ...ACTIONS.map(([id]) => rowOf(id)), h("span", { class: "help" }, t("r21.sc.edit_help")));
}

export function openHelp() {
  const dlg = $("#helpDlg");
  dlg.replaceChildren(
    h("div", { class: "dlg-head" }, h("h2", {}, t("help.title"))),
    h("div", { class: "dlg-body" }, shortcutList()),
    h("div", { class: "dlg-foot" }, h("button", { class: "btn", onclick: () => dlg.close() }, t("ui.close"))));
  dlg.showModal();
}

// 言語の選択肢: 先頭は「Auto(OS に合わせる、今は ○○)」、続けて各言語を、その言語の名前で
const langItems = () => [["auto", t("set.lang_auto", { name: LANGS.find(([code]) => code === S.osLang)?.[1] ?? "English" }), true],
  ...LANGS.map(([code, name]) => [code, name, true])];

const seg = (items, current, onPick) => {
  const wrap = h("div", { class: "seg", role: "group" });
  const paint = (value) => wrap.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === value)));
  for (const [value, label, plain] of items) {
    wrap.append(h("button", { dataset: { v: value }, onclick: () => { paint(value); onPick(value); } }, plain ? label : t(label)));
  }
  paint(current);
  return wrap;
};

const STATE_TEXT = { ready: "set.state.ready", waking: "set.state.waking", stopped: "set.state.stopped" };
const EFFORT_TEXT = { low: "set.effort.low", medium: "set.effort.medium", high: "set.effort.high" };

/** モデルの選び方: よく使う ID の候補から選ぶか、「ほかの ID」で自由に入力する。 */
function modelPicker(provider, current) {
  const OTHER = "__other";
  const choices = S.modelChoices[provider] || [];
  const sel = h("select", { class: "field-input", "aria-label": t("set.model") },
    ...choices.map((m) => h("option", { value: m }, m === S.defaultModels[provider] ? t("set.default_tag", { m }) : m)),
    h("option", { value: OTHER }, t("set.other_id")));
  const free = h("input", { class: "field-input", placeholder: t("set.model_id"), spellcheck: "false", "aria-label": t("set.model_id") });
  const known = choices.includes(current);
  sel.value = known ? current : OTHER;
  free.value = known ? "" : current || "";
  const paint = () => free.classList.toggle("hidden", sel.value !== OTHER);
  sel.addEventListener("change", paint);
  paint();
  return { el: h("div", { class: "set-group", style: "gap:6px" }, sel, free), inputs: [sel, free],
    value: () => (sel.value === OTHER ? free.value.trim() : sel.value) || S.defaultModels[provider] };
}

/** 音量と話す速さ。声の切り替えと保存は入力欄の上にある。速さは次に作る声から変わる。 */
function volumeSection() {
  const volume = h("input", { type: "range", class: "range", min: "0", max: "100", value: String(Math.round(getVolume() * 100)), "aria-label": t("set.volume") });
  volume.addEventListener("input", () => setVolume(volume.value / 100));
  const shown = (v) => t("set.times", { n: num(Number(v), 2) });
  const value = h("span", { class: "range-value" }, shown(S.config.speech_speed ?? 1));
  const speed = h("input", { type: "range", class: "range", min: "0.7", max: "1.6", step: "0.05", value: String(S.config.speech_speed ?? 1), "aria-label": t("set.speed") });
  speed.addEventListener("input", () => { value.textContent = shown(speed.value); });
  speed.addEventListener("change", async () => {
    try { S.config = await api("PUT", "/api/config", { speech_speed: Number(speed.value) }); } catch (e) { toast(e.message, { kind: "err" }); }
  });
  return h("div", { class: "set-group" },
    h("label", { class: "range-row" }, h("span", { class: "set-label" }, t("set.volume")), volume),
    h("label", { class: "range-row" }, h("span", { class: "set-label" }, t("set.speed")), speed, value));
}

/** 行ごとの「残す」(★)。その行の音声を WAV と JSON で保存先に残す。入力欄の上の声の登録とは別。 */
function starButton(line) {
  const btn = h("button", { type: "button", class: "btn small past-star", onclick: () => toggleStar(line) }, iconEl("star"), h("span", {}));
  btn._paint = () => {
    btn.setAttribute("aria-pressed", String(Boolean(line.starred)));
    btn.title = t(line.starred ? "set.keep_off_title" : "set.keep_line_title");
    btn.lastChild.textContent = t(line.starred ? "ctl.kept_label" : "ui.keep_audio");
  };
  btn._paint();
  return btn;
}

/** これまでの文章。ふだんは閉じておく。各行から、その行の音声を残せる。設定を開いている間も、鳴った行が増えれば足す。 */
function pastLines() {
  const summary = h("summary", {});
  const list = h("div", { class: "past-list" });
  const box = h("details", { class: "past" }, summary, list);
  const rowsOf = () => S.live.log.filter((e) => e.kind === "user" || (e.kind === "line" && ["playing", "played"].includes(e.state)));
  let shown = -1;
  const paint = () => {
    const rows = rowsOf();
    shown = rows.length;
    summary.textContent = t("set.past", { n: rows.filter((e) => e.kind === "line").length });
    list.replaceChildren(...(rows.length
      ? rows.map((e) => (e.kind === "user"
        ? h("div", { class: "past-user" }, e.text)
        : h("div", { class: "past-row" }, h("span", { class: "past-line" }, e.text), starButton(e))))
      : [h("div", { class: "help" }, t("set.none_yet"))]));
  };
  // 行が増えた・消えた時は作り直し、★ だけ変わった時はボタンの表示だけ直す
  box._sync = () => (rowsOf().length === shown ? list.querySelectorAll(".past-star").forEach((b) => b._paint()) : paint());
  paint();
  return box;
}

for (const ev of ["live-entry", "live-line", "live-reset"]) on(ev, () => document.querySelectorAll(".past").forEach((b) => b._sync?.()));

export function closeSettings() {
  $("#drawer").classList.add("hidden");
  $("#scrim").classList.add("hidden");
  pending = null;
}

export const settingsOpen = () => !$("#drawer").classList.contains("hidden");

// 開いている設定の「未保存か」と「閉じる前の確認を出す / 戻す」。閉じる操作(Esc、背景、閉じるボタン)は必ず requestCloseSettings を通す
let pending = null;

/** 未保存の変更がある時は、閉じずに「保存 / 破棄 / 戻る」を出す。確認の最中にもう一度閉じる操作をすると「戻る」。 */
export function requestCloseSettings() {
  if (!pending) { closeSettings(); return; }
  if (pending.confirming()) pending.back();
  else if (pending.dirty()) pending.ask();
  else closeSettings();
}

export async function openSettings(section, restore = null) {
  // すでに開いている時は作り直さない(書きかけの内容が消えるため)
  if (settingsOpen()) {
    if (section === "agent") $("#setAgent").scrollIntoView({ block: "start" });
    return;
  }
  const c = S.config;
  const drawer = $("#drawer");
  const text = (value, ph = "") => h("input", { class: "field-input", value: value ?? "", placeholder: ph, spellcheck: "false" });
  const f = {
    lmUrl: text(c.llm.lmstudio.base_url, "http://localhost:1234/v1"), lmModel: text(c.llm.lmstudio.model, t("set.lm_model_ph")),
    exe: text(c.speech_exe, t("set.exe_ph")), modelDir: text(c.model_dir, t("set.modeldir_ph")),
    out: text(c.output_dir), voicesDir: text(c.voices_dir),
    voicePath: text("", t("set.voicepath_ph")),
  };
  const row = (label, input) => h("div", { class: "set-group", style: "gap:6px" }, h("label", {}, label), input);
  // 音声合成のモデル(speech.cpp が扱えるもの全部)、読み上げる言語、内蔵の声(Qwen3-TTS などの、声を選ぶモデル)
  const langName = (code) => { try { return new Intl.DisplayNames([getLang()], { type: "language" }).of(code) || code; } catch { return code; } };
  const modelSel = h("select", { class: "field-input", "aria-label": t("set.model_name") }, h("option", { value: c.model }, c.model));
  const speakSel = h("select", { class: "field-input", "aria-label": t("set.speak_language") });
  const ttsVoiceSel = h("select", { class: "field-input", "aria-label": t("set.tts_voice") });
  const ttsVoiceRow = row(t("set.tts_voice"), ttsVoiceSel);
  let tts = { models: [], loaded: {} };
  const paintTts = () => {
    const chosen = tts.models.find((m) => m.name === modelSel.value);
    const langs = chosen ? chosen.languages : (tts.loaded.languages?.length ? tts.loaded.languages : ["ja"]);
    const wantLang = speakSel.value || c.speak_language;
    speakSel.replaceChildren(h("option", { value: "auto" }, t("set.speak_auto")), ...langs.map((l) => h("option", { value: l }, langName(l))));
    speakSel.value = langs.includes(wantLang) ? wantLang : "auto";
    // 内蔵の声は、読み込んであるモデルの分だけ分かる(別のモデルを選んだら、保存して再起動したあとに出る)
    const presets = modelSel.value === S.config.model ? (tts.loaded.presets || []) : [];
    ttsVoiceRow.classList.toggle("hidden", !presets.length);
    const wantVoice = ttsVoiceSel.value || c.tts_voice || "";
    ttsVoiceSel.replaceChildren(h("option", { value: "" }, t("set.tts_voice_auto")),
      ...presets.map((v) => h("option", { value: v.name }, `${v.name} (${langName(v.language)}${v.gender ? ", " + t(`set.gender_${v.gender}`) : ""})`)));
    ttsVoiceSel.value = presets.some((v) => v.name === wantVoice) ? wantVoice : "";
  };
  const modelLabel = (m) => {
    const langs = m.languages.length > 3 ? t("set.langs_n", { n: m.languages.length }) : m.languages.map(langName).join(", ");
    return `${m.name} · ${langs} · ${m.size}${m.fetched ? "" : " · " + t("set.not_fetched")}`;
  };
  api("GET", "/api/tts/models").then((r) => {
    tts = r;
    if (r.models.length) {
      modelSel.replaceChildren(...r.models.map((m) => h("option", { value: m.name }, modelLabel(m))));
      if (!r.models.some((m) => m.name === c.model)) modelSel.prepend(h("option", { value: c.model }, c.model));
      modelSel.value = c.model;
    }
    paintTts();
  }).catch(() => paintTts());
  const ttsDirty = () => unsaved.classList.remove("hidden");
  modelSel.addEventListener("change", () => { paintTts(); ttsDirty(); });
  speakSel.addEventListener("change", ttsDirty);
  ttsVoiceSel.addEventListener("change", ttsDirty);
  const claudePick = modelPicker("claude", c.llm.claude.model);
  const codexPick = modelPicker("codex", c.llm.codex.model);
  const effortSel = h("select", { class: "field-input", "aria-label": t("set.codex_effort_aria") },
    ...(S.efforts.length ? S.efforts : ["low", "medium", "high"]).map((e) => h("option", { value: e }, EFFORT_TEXT[e] ? t(EFFORT_TEXT[e]) : e)));
  effortSel.value = c.llm.codex.effort || "low";
  const deviceSel = h("select", { class: "field-input", "aria-label": t("set.device") }, h("option", { value: "auto" }, t("set.auto")));
  const modelList = h("datalist", { id: "lmModels" });
  f.lmModel.setAttribute("list", "lmModels");
  const result = h("div", { class: "test-result hidden" });
  const agentState = h("div", { class: "help" });
  const voicesBox = h("div", { class: "set-group" });
  const defaultHow = h("input", { class: "field-input", maxlength: "200", value: S.config.default_how || "", placeholder: t("set.default_how_ph") });

  // 選んでいる LLM の設定だけを出す(選んでいない LLM のモデル欄は出さない。値は保存の時にまとめて送る)
  const providerBox = h("div", { class: "set-group" });
  const paintProvider = (provider) => {
    const box = {
      claude: [row(t("set.claude_model"), claudePick.el)],
      codex: [row(t("set.codex_model"), codexPick.el), row(t("set.codex_effort"), effortSel),
        h("span", { class: "help" }, t("set.codex_effort_help"))],
      lmstudio: [row(t("set.lm_url"), f.lmUrl), row(t("set.lm_model"), f.lmModel), modelList],
    }[provider] || [];
    providerBox.replaceChildren(...box);
    web.disabled = provider === "lmstudio";
    webHelp.textContent = t(provider === "lmstudio" ? "r21.web_search_lm" : "r21.web_search_help");
  };
  const web = h("input", { type: "checkbox" });
  web.checked = c.web_search !== false;
  const webHelp = h("span", { class: "help" });
  const webBox = h("div", { class: "set-group" }, h("label", { class: "switch" }, web, h("span", { class: "sw" }), t("r21.web_search")), webHelp);
  const llmPatch = () => ({ claude: { model: claudePick.value() }, codex: { model: codexPick.value(), effort: effortSel.value },
    lmstudio: { base_url: f.lmUrl.value.trim(), model: f.lmModel.value.trim() } });
  const saveConfig = async (patch) => { S.config = await api("PUT", "/api/config", patch); emit("config-changed"); };
  const unsaved = h("span", { class: "foot-note hidden" }, t("set.unsaved"));
  const prompt = {};
  const promptValue = (key) => S.config.prompts?.[key] || S.promptDefaults[key] || "";
  const promptField = (key) => {
    const label = t(`prompt.${key}.label`);
    const area = h("textarea", { class: "field-input style-input", rows: key === "style" || key === "format" ? "6" : "3",
      spellcheck: "false", maxlength: String(S.promptLimits[key] || 2000), "aria-label": label });
    area.value = promptValue(key);
    prompt[key] = area;
    const vars = (S.promptVars[key] || []).map((v) => `{${v}} ${t(`var.${v}`)}`).join(getLang() === "en" ? ", " : "、");
    return h("div", { class: "set-group", style: "gap:6px" },
      h("div", { class: "prompt-head" }, h("label", {}, label),
        h("button", { class: "btn small", onclick: () => { area.value = S.promptDefaults[key]; unsaved.classList.remove("hidden"); } }, t("set.reset"))),
      area,
      h("span", { class: "help" }, t(`prompt.${key}.about`), vars ? ` ${t("set.fill_in")} ${vars}` : ""));
  };
  // 文面をまとめて既定に戻す: 確認を出し、戻した内容は保存前の状態(「破棄して閉じる」で取り消せる)
  const resetAllBox = h("div", { class: "reset-all" });
  const resetAllIdle = () => resetAllBox.replaceChildren(h("button", { type: "button", class: "btn small", onclick: resetAllAsk }, iconEl("rotate-ccw"), t("r21.reset_all")));
  const resetAllAsk = () => {
    resetAllBox.replaceChildren(h("div", { class: "reset-ask", role: "alert" }, t("r21.reset_all_ask")),
      h("div", { class: "set-row" },
        h("button", { type: "button", class: "btn small danger", onclick: resetAllDo }, t("r21.reset_all_do")),
        h("button", { type: "button", class: "btn small", onclick: resetAllIdle }, t("ui.cancel"))));
    resetAllBox.querySelector(".danger").focus();
  };
  const resetAllDo = async () => {
    let defaults = S.promptDefaults;
    try {
      const r = await api("POST", "/api/prompts/reset", {});
      defaults = r.prompts ?? r.defaults ?? r;
    } catch { /* 無ければ、起動時に受け取った既定の一式を使う */ }
    for (const [key, area] of Object.entries(prompt)) area.value = typeof defaults[key] === "string" ? defaults[key] : S.promptDefaults[key] ?? "";
    S.promptDefaults = { ...S.promptDefaults, ...Object.fromEntries(Object.entries(defaults).filter(([, v]) => typeof v === "string")) };
    unsaved.classList.remove("hidden");
    resetAllIdle();
    toast(t("r21.reset_all_done"));
  };
  resetAllIdle();
  const paintAgent = () => {
    const login = S.llm.auth.state === "ok" ? t("set.state_login") : "";
    agentState.textContent = t("set.state", { state: STATE_TEXT[S.llm.state] ? t(STATE_TEXT[S.llm.state]) : S.llm.state,
      reason: S.llm.ok ? "" : t("set.state_reason", { reason: S.llm.reason }), login });
  };
  paintAgent();
  on("llm", () => { if (agentState.isConnected) paintAgent(); });

  const refreshVoices = async () => { S.voices = (await api("GET", "/api/voices")).voices; emit("voices"); paintVoices(); };
  const setDefault = async (name) => {
    try {
      await saveConfig({ default_voice: name });
      await refreshVoices();
      toast(name ? t("set.default_next", { name }) : t("set.default_none"));
    } catch (e) { toast(e.message, { kind: "err" }); }
  };
  const paintVoices = () => {
    voicesBox.replaceChildren(...(S.voices.length ? S.voices.map((v) => h("div", { class: "voice-row" },
      h("span", { class: "voice-row-name" }, v.name, v.default ? h("span", { class: "badge" }, t("set.badge_next")) : null),
      h("span", { class: "voice-row-acts" },
        v.default
          ? h("button", { class: "btn small", onclick: () => setDefault("") }, t("set.unset_next"))
          : h("button", { class: "btn small", onclick: () => setDefault(v.name) }, t("set.set_next")),
        h("button", { class: "btn small danger", onclick: async (e) => {
          const btn = e.currentTarget;
          if (!btn.dataset.sure) {
            btn.dataset.sure = "1";
            btn.lastChild.textContent = t("set.delete_sure");
            setTimeout(() => { if (btn.isConnected) { delete btn.dataset.sure; btn.lastChild.textContent = t("set.delete"); } }, 4000);
            return;
          }
          try {
            S.voices = (await api("POST", "/api/voices/delete", { name: v.name })).voices;
            emit("voices");
            paintVoices();
          } catch (e) { toast(e.message, { kind: "err" }); }
        } }, iconEl("trash-2"), t("set.delete"))))) : [h("div", { class: "help" }, t("set.no_voices"))]));
  };
  paintVoices();

  // 詳しい設定。項目の定義(型・範囲・説明)はサーバーの tuning.py にあり、ここでは定義から画面を作る
  const tun = {};
  const tuningField = (item) => {
    const label = t(`tuning.${item.key}.label`);
    const choiceName = (v) => t(`tuning.choice.${v}`);
    const dirty = () => unsaved.classList.remove("hidden");
    const now = S.config.tuning?.[item.key] ?? item.default;
    let el, get, put;
    if (item.kind === "bool") {
      el = h("input", { type: "checkbox", "aria-label": label });
      get = () => el.checked;
      put = (v) => { el.checked = Boolean(v); };
    } else if (item.kind === "choice") {
      el = h("select", { class: "field-input", "aria-label": label }, ...item.choices.map((v) => h("option", { value: v }, choiceName(v))));
      get = () => el.value;
      put = (v) => { el.value = v; };
    } else if (item.kind === "int" || item.kind === "float") {
      el = h("input", { class: "field-input", type: "number", min: String(item.min), max: String(item.max),
        step: item.kind === "int" ? "1" : "any", "aria-label": label });
      get = () => (el.value.trim() === "" ? null : Number(el.value));
      put = (v) => { el.value = String(v); };
    } else {
      el = h("input", { class: "field-input", spellcheck: "false", maxlength: String(item.max), "aria-label": label,
        placeholder: item.default === "" ? t("set.empty") : "" });
      if (item.kind === "regex") el.classList.add("mono");
      get = () => el.value;
      put = (v) => { el.value = v; };
    }
    put(now);
    el.addEventListener(item.kind === "bool" || item.kind === "choice" ? "change" : "input", dirty);
    tun[item.key] = { get };
    const shownDefault = item.kind === "bool" ? t(item.default ? "set.on" : "set.off")
      : item.kind === "choice" ? choiceName(item.default) : item.default === "" ? t("set.empty") : typeof item.default === "number" ? num(item.default, 6) : String(item.default);
    const reset = h("button", { class: "btn small", title: t("set.default_title", { value: shownDefault }), onclick: () => { put(item.default); dirty(); } }, t("set.reset"));
    const control = item.kind === "bool" ? h("label", { class: "switch" }, el, h("span", { class: "sw" }), label) : el;
    return h("div", { class: "set-group tune", style: "gap:6px" },
      item.kind === "bool" ? null : h("label", {}, label),
      h("div", { class: "tune-row" }, control, reset),
      h("span", { class: "help" }, t(`tuning.${item.key}.about`), " ", t("set.default_is", { value: shownDefault })));
  };

  const saveAll = async (restart) => {
    try {
      await saveConfig({ speech_exe: f.exe.value.trim(), model_dir: f.modelDir.value.trim(), model: modelSel.value,
        speak_language: speakSel.value || "auto", tts_voice: ttsVoiceSel.value,
        device: deviceSel.value, output_dir: f.out.value.trim(), voices_dir: f.voicesDir.value.trim(), llm: llmPatch(), web_search: web.checked,
        default_how: defaultHow.value.trim(),
        tuning: Object.fromEntries(Object.entries(tun).map(([k, t]) => [k, t.get()])),
        prompts: Object.fromEntries(Object.entries(prompt).map(([k, area]) => [k, area.value])) });
      for (const [k, area] of Object.entries(prompt)) area.value = promptValue(k);
      if (!restart) await refreshVoices();
      unsaved.classList.add("hidden");
      if (restart) await api("POST", "/api/engine/restart", {});
      toast(t(restart ? "set.save_restarting" : "set.saved"));
      return true;
    } catch (e) {
      toast(t("set.save_failed", { e: e.message }), { kind: "err" });
      return false;
    }
  };

  const testBtn = h("button", { class: "btn small", onclick: async () => {
    testBtn.disabled = true;
    testBtn.replaceChildren(iconEl("loader-circle", "spin"), t("set.checking"));
    try {
      // 保存はしない: 画面に入っている値で試す(「破棄して閉じる」で元に戻る)
      const r = await api("POST", "/api/llm/test", { provider: S.config.llm_provider, llm: llmPatch() });
      result.className = `test-result ${r.ok ? "ok" : "ng"}`;
      result.replaceChildren(t("set.test_line", { head: t(r.ok ? "set.test_ok" : "set.test_ng"), sec: num(r.seconds, 1), detail: r.detail }),
        r.next ? h("div", { class: "test-next" }, t("set.test_next", { next: r.next })) : null,
        r.raw ? h("details", { class: "test-raw" }, h("summary", {}, t("set.test_raw")), h("pre", {}, r.raw)) : null);
      if (r.models?.length) modelList.replaceChildren(...r.models.map((m) => h("option", { value: m })));
    } catch (e) {
      result.className = "test-result ng";
      result.textContent = t("set.test_fail", { e: e.message });
    } finally {
      testBtn.disabled = false;
      testBtn.replaceChildren(iconEl("plug"), t("set.test"));
    }
  } }, iconEl("plug"), t("set.test"));

  const keep = h("input", { type: "checkbox" });
  keep.checked = Boolean(c.keep_all);
  keep.addEventListener("change", () => saveConfig({ keep_all: keep.checked }).catch((e) => toast(e.message, { kind: "err" })));
  const asr = h("input", { type: "checkbox" });
  asr.checked = Boolean(c.asr_check);
  asr.addEventListener("change", () => saveConfig({ asr_check: asr.checked }).catch((e) => toast(e.message, { kind: "err" })));

  const footButtons = [unsaved,
    h("button", { class: "btn", id: "drawerClose", onclick: requestCloseSettings }, t("ui.close")),
    h("button", { class: "btn primary", id: "drawerSave", onclick: () => saveAll(false) }, t("set.save"))];
  const foot = h("div", { class: "dlg-foot" }, ...footButtons);
  let confirming = false;
  pending = {
    dirty: () => !unsaved.classList.contains("hidden"),
    confirming: () => confirming,
    back: () => { confirming = false; foot.classList.remove("confirming"); foot.replaceChildren(...footButtons); $("#drawerClose").focus(); },
    ask: () => {
      confirming = true;
      foot.classList.add("confirming");
      foot.replaceChildren(
        h("span", { class: "foot-note", role: "alert" }, t("set.unsaved_dot")),
        h("button", { class: "btn danger", onclick: closeSettings }, t("set.discard")),
        h("button", { class: "btn", id: "confirmBack", onclick: () => pending.back() }, t("ui.back")),
        h("button", { class: "btn primary", onclick: async () => { if (await saveAll(false)) closeSettings(); } }, t("set.save_close")));
      $("#confirmBack").focus();
    },
  };
  const openFolder = (which) => api("POST", "/api/open", { which }).catch((e) => toast(e.message, { kind: "err" }));
  const formFields = () => [...drawer.querySelectorAll("input, textarea, select")];
  // 開いている設定の入力値を写す(言語を替えて開き直す時に、書きかけを引き継ぐ)
  const snapshotForm = () => ({
    values: formFields().map((el) => (el.type === "checkbox" ? el.checked : el.value)),
    open: [...drawer.querySelectorAll("details")].map((d) => d.open),
    scroll: drawer.querySelector(".dlg-body").scrollTop,
    dirty: pending ? pending.dirty() : false,
    device: deviceSel.value,
  });
  // 言語はその場で保存し、設定を同じ内容(書きかけも含む)で開き直して、画面全体を新しい言語にする
  const changeLanguage = async (code) => {
    if (code === S.config.language) return;
    try {
      await saveConfig({ language: code });
      const snap = snapshotForm();
      setLang(code === "auto" ? S.osLang : code);
      closeSettings();
      emit("lang");
      await openSettings(undefined, snap);
    } catch (e) { toast(e.message, { kind: "err" }); }
  };
  drawer.replaceChildren(
    h("div", { class: "dlg-head" }, h("h2", {}, t("set.title"))),
    h("div", { class: "dlg-body" },
      h("section", { class: "set-group", id: "setLanguage" }, h("h3", {}, "Language / 言語"),
        seg(langItems(), c.language, changeLanguage),
        h("span", { class: "help" }, t("set.language_hint"))),
      // 1. 文章を書く LLM: 切り替え → 状態 → ログイン → 選んだ LLM のモデル → 接続テスト → 文章の書き方
      h("section", { class: "set-group", id: "setAgent" }, h("h3", {}, t("set.llm")),
        seg(PROVIDERS, c.llm_provider, (v) => saveConfig({ llm_provider: v }).then(() => { paintAgent(); paintProvider(v); }).catch((e) => toast(e.message, { kind: "err" }))),
        agentState,
        loginBlock(),
        providerBox,
        webBox,
        h("div", { class: "set-row" }, testBtn), result),
      h("section", { class: "set-group" }, h("h3", {}, t("set.writing")),
        promptField(PROMPTS[0]),
        h("span", { class: "help" }, t("set.writing_hint")),
        resetAllBox),
      // 2. 声と再生
      h("section", { class: "set-group" }, h("h3", {}, t("set.default_how")),
        defaultHow,
        h("span", { class: "help" }, t("set.default_how_help"))),
      h("section", { class: "set-group" }, h("h3", {}, t("set.volume_speed")), volumeSection()),
      h("section", { class: "set-group" }, h("h3", {}, t("set.saved_voices")),
        voicesBox,
        row(t("set.voices_dir"), f.voicesDir),
        h("span", { class: "help" }, t("set.voices_help")),
        h("div", { class: "set-row" }, h("button", { class: "btn small", onclick: () => openFolder("voices") }, iconEl("folder-open"), t("set.open_voices"))),
        row(t("set.import_from"), f.voicePath),
        h("div", { class: "set-row" }, h("button", { class: "btn small", onclick: async () => {
          try {
            const r = await api("POST", "/api/voices/import", { path: f.voicePath.value.trim() });
            S.voices = r.voices;
            emit("voices");
            paintVoices();
            toast(t("set.imported", { n: r.copied.length, skipped: r.skipped.length ? t("set.imported_skipped", { n: r.skipped.length }) : "" }));
          } catch (e) { toast(e.message, { kind: "err" }); }
        } }, iconEl("folder-open"), t("set.import")))),
      // 3. 音声モデル
      h("section", { class: "set-group" }, h("h3", {}, t("set.speech")),
        row(t("set.exe"), f.exe), row(t("set.model_dir"), f.modelDir), row(t("set.model_name"), modelSel),
        h("span", { class: "help" }, t("set.model_help")),
        row(t("set.speak_language"), speakSel), ttsVoiceRow, row(t("set.device"), deviceSel),
        h("label", { class: "switch" }, asr, h("span", { class: "sw" }), t("set.asr")),
        h("span", { class: "help" }, t("set.asr_help")),
        h("div", { class: "set-row" }, h("button", { class: "btn small", onclick: () => saveAll(true) }, iconEl("rotate-ccw"), t("set.save_restart")))),
      // 4. 残す音声
      h("section", { class: "set-group" }, h("h3", {}, t("set.audio_files")),
        h("label", { class: "switch" }, keep, h("span", { class: "sw" }), t("set.keep_all")),
        h("span", { class: "help" }, t("set.keep_help")),
        row(t("set.out_dir"), f.out),
        h("div", { class: "set-row" }, h("button", { class: "btn small", onclick: () => openFolder("output") }, iconEl("folder-open"), t("set.open_out"))),
        pastLines()),
      // 5. 見た目・ショートカット・注意事項
      h("section", { class: "set-group" }, h("h3", {}, t("set.look")),
        h("span", { class: "set-label" }, t("set.theme")),
        seg(THEMES, themePref(), (v) => {
          try { localStorage.setItem("irodori.theme", v); } catch { /* 保存できなくても動く */ }
          applyTheme(v);
        }),
        h("span", { class: "set-label" }, t("set.color")),
        seg(COLORS, document.documentElement.dataset.color, (v) => setLook("color", v)),
        h("span", { class: "set-label" }, t("set.subs")),
        seg(SUBS, document.documentElement.dataset.subs, (v) => setLook("subs", v))),
      h("section", { class: "set-group", id: "setShortcuts" }, h("h3", {}, t("set.shortcuts")), shortcutEditor()),
      // 6. 詳しい設定(ふだんは閉じておく)。LLM や Irodori に渡る文と、出力を左右する値は、すべてここで変えられる
      h("section", { class: "set-group", id: "setAdvanced" },
        h("details", { class: "past advanced" }, h("summary", {}, t("set.advanced")),
          h("div", { class: "set-group" },
            h("span", { class: "help" }, t("set.advanced_help")),
            h("details", { class: "past sub" }, h("summary", {}, t("set.other_prompts")),
              h("div", { class: "set-group" },
                h("span", { class: "help" }, t("set.other_prompts_help")),
                ...PROMPTS.slice(1).map(promptField))),
            ...S.tuningSpec.filter((i) => i.shown).map(tuningField),
            h("span", { class: "help" }, t("set.advanced_more")))))),
    foot);
  for (const el of [...Object.values(f).filter((x) => x !== f.voicePath), ...Object.values(prompt), deviceSel, effortSel, ...claudePick.inputs, ...codexPick.inputs]) el.addEventListener("input", () => unsaved.classList.remove("hidden"));
  web.addEventListener("change", () => unsaved.classList.remove("hidden"));
  paintProvider(c.llm_provider);
  drawer.classList.remove("hidden");
  $("#scrim").classList.remove("hidden");
  $("#drawerClose").focus();
  if (restore) {
    formFields().forEach((el, i) => {
      const v = restore.values[i];
      if (v === undefined) return;
      if (el.type === "checkbox") el.checked = v;
      else el.value = v;
      if (el.tagName === "SELECT") el.dispatchEvent(new Event("change"));
    });
    drawer.querySelectorAll("details").forEach((d, i) => { d.open = Boolean(restore.open[i]); });
    if (restore.dirty) unsaved.classList.remove("hidden");
    drawer.querySelector(".dlg-body").scrollTop = restore.scroll;
  }
  if (section === "agent") $("#setAgent").scrollIntoView({ block: "start" });

  try {
    const { devices } = await api("GET", "/api/devices");
    for (const d of devices) deviceSel.append(h("option", { value: d.name }, `${d.name} (${d.kind.toUpperCase()}: ${d.info})`));
    const wanted = restore?.device || c.device;
    deviceSel.value = wanted;
    if (deviceSel.value !== wanted) deviceSel.value = "auto";
  } catch { /* 一覧が取れなくても auto で使える */ }
}
