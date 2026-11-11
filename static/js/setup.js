import { iconEl } from "./icons.js";
import { getLang, t } from "./i18n.js";
import { loginBlock, needsLogin } from "./login.js";
import { $, S, api, emit, h, on, toast } from "./state.js";

/* 最初の画面に用意のしかたを出す。speech.cpp(音声を作る)が見つからない時と、文章を書く LLM が使えない時。 */
const INSTALL_DOC = "https://github.com/nyosegawa/speech.cpp/blob/main/docs/install.md";
const INSTALL_CMD = {
  windows: { shell: "pwsh", cmd: "irm https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/install.ps1 | iex" },
  mac: { shell: "terminal", cmd: "curl -fsSL https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/install.sh | sh" },
  linux: { shell: "terminal", cmd: "curl -fsSL https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/install.sh | sh" },
};
const pullCmd = () => `speech pull ${S.config.model || "irodori-tts-mf"}`;
// 最初に入るモデルの大きさ(GB)。日本語は Irodori-TTS、ほかの言語は Qwen3-TTS
const MODEL_GB = { "irodori-tts-mf": "1.9", "qwen3-tts-0.6b": "1.2" };
const modelGb = () => MODEL_GB[S.config.model] || "1–2";

const CHOICES = [
  { id: "claude", name: "Claude Code",
    url: "https://docs.anthropic.com/en/docs/claude-code/setup" },
  { id: "codex", name: "Codex",
    url: "https://github.com/openai/codex" },
  { id: "lmstudio", name: "LM Studio",
    url: "https://lmstudio.ai" },
];

let result = null;

async function use(choice, btn) {
  btn.disabled = true;
  btn.replaceChildren(iconEl("loader-circle", "spin"), t("setup.checking"));
  try {
    S.config = await api("PUT", "/api/config", { llm_provider: choice.id });
    emit("config-changed");
    const r = await api("POST", "/api/llm/test", { provider: choice.id });
    result = { id: choice.id, ok: r.ok, text: r.ok ? t("setup.works", { name: choice.name }) : r.detail };
  } catch (e) {
    result = { id: choice.id, ok: false, text: e.message };
  }
  render();
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = t("ui.copied"); } catch { btn.textContent = t("ui.copy_manual"); }
  setTimeout(() => { btn.textContent = t("ui.copy"); }, 2000);
}

const command = (text) => {
  const btn = h("button", { class: "btn small", onclick: () => copy(text, btn) }, t("ui.copy"));
  return h("div", { class: "cmd" }, h("code", {}, text), btn);
};

async function recheck(btn) {
  btn.disabled = true;
  try { await api("POST", "/api/engine/restart"); } catch (e) { toast(e.message, { kind: "err" }); }
  btn.disabled = false;
}

/** 「セットアップ」: 足りない speech.cpp とモデルだけを入れる(入っているものは使う)。進み具合は SSE の setup で届く。 */
async function autoInstall(btn) {
  btn.disabled = true;
  try { S.setup = await api("POST", "/api/setup/install", {}); } catch (e) { toast(e.message, { kind: "err" }); }
  render();
}

function autoRow(lead) {
  const st = S.setup || { state: "idle" };
  const running = st.state === "running";
  const btn = h("button", { class: "btn primary", onclick: () => autoInstall(btn) },
    running ? [iconEl("loader-circle", "spin"), t("setup.auto_running")] : t(st.state === "error" ? "setup.auto_retry" : "setup.auto"));
  btn.disabled = running;
  const step = running && st.step ? t(`setup.auto_step_${st.step}`) + (st.percent != null ? ` ${st.percent}%` : "") : "";
  return h("div", { class: "setup-row" },
    h("div", { class: "setup-text" },
      h("div", {}, lead),
      step ? h("div", { class: "setup-step" }, step, st.line ? h("div", {}, st.line) : null) : null,
      st.state === "error" ? h("div", { class: "test-result ng" }, st.error) : null),
    btn);
}

/** speech.cpp かモデルが無い時のセットアップ。ボタン 1 つの自動と、README と同じ手で入れる方法。 */
function speechCard() {
  const how = { ...(INSTALL_CMD[S.platform] || INSTALL_CMD.windows) };
  how.shell = t(`setup.cmd.${how.shell}`);
  const noModel = S.engine.missing === "model";
  const retry = h("button", { class: "btn small", onclick: () => recheck(retry) }, t("setup.retry"));
  return h("div", { class: "setup" },
    h("h2", {}, t(noModel ? "setup.model_title" : "setup.speech_title")),
    autoRow(t(noModel ? "setup.model_lead" : "setup.speech_lead", { gb: modelGb() })),
    h("details", { class: "setup-manual" },
      h("summary", {}, t("setup.manual")),
      noModel ? null : h("div", { class: "setup-row" },
        h("div", { class: "setup-text" },
          h("div", { class: "setup-name" }, t("setup.step1", { shell: how.shell })),
          command(how.cmd),
          h("div", { class: "setup-step" }, t("setup.step1_after", { shell: how.shell }), " ", h("a", { href: INSTALL_DOC, target: "_blank", rel: "noreferrer" }, t("setup.details"))))),
      h("div", { class: "setup-row" },
        h("div", { class: "setup-text" },
          h("div", { class: "setup-name" }, t(noModel ? "setup.step_model" : "setup.step2")),
          command(pullCmd()))),
      h("div", { class: "setup-row" },
        h("div", { class: "setup-text" },
          h("div", { class: "setup-step" }, t("setup.elsewhere"))),
        h("button", { class: "btn small", onclick: () => emit("open-settings") }, t("ui.open_settings")),
        retry)));
}

const langName = (code) => { try { return new Intl.DisplayNames([getLang()], { type: "language" }).of(code) || code; } catch { return code; } };

/** 声のモデルを選ぶ。選ぶと設定に入り、「セットアップ」でそのモデルが入る(入っていれば、すぐ使える)。 */
async function pickModel(id) {
  try {
    S.config = await api("PUT", "/api/config", { audio_pick: id, audio_model: "" });
    await api("POST", "/api/engine/restart", {});
  } catch (e) { toast(e.message, { kind: "err" }); }
}

function modelRows() {
  const busy = (S.setup || {}).state === "running";
  return h("div", { class: "model-list", role: "radiogroup", "aria-label": t("setup.model_pick") },
    ...(S.engine.audio_models || []).map((m) => {
      const langs = m.languages.length > 4 ? t("setup.model_langs_n", { first: m.languages.slice(0, 3).map(langName).join(t("setup.sep")), n: m.languages.length }) : m.languages.map(langName).join(t("setup.sep"));
      const voice = m.design ? t("setup.model_design") : t("setup.model_voices", { n: m.voices });
      return h("button", { type: "button", class: "model-row", role: "radio", "aria-checked": String(m.picked), disabled: busy, onclick: () => { if (!m.picked) pickModel(m.id); } },
        h("span", { class: "model-mark" }),
        h("span", { class: "model-text" },
          h("span", { class: "model-name" }, m.name, m.installed ? h("span", { class: "setup-now" }, t("setup.model_installed")) : null),
          h("span", { class: "model-meta" }, `${langs} · ${voice}`)),
        h("span", { class: "model-size" }, t("setup.model_gb", { gb: m.gb })));
    }));
}

/** audio.cpp を選んでいて、実行ファイルかモデルが無い時のセットアップ。 */
function audioCard() {
  const noModel = S.engine.missing === "audio_model";
  const retry = h("button", { class: "btn small", onclick: () => recheck(retry) }, t("setup.retry"));
  const picked = (S.engine.audio_models || []).find((m) => m.picked);
  return h("div", { class: "setup" },
    h("h2", {}, t("setup.speech_title")),
    h("p", { class: "setup-lead" }, t("setup.model_pick_lead")),
    modelRows(),
    autoRow(t(noModel ? "setup.audio_model_lead" : "setup.audio_lead", { gb: picked ? picked.gb : "" })),
    h("details", { class: "setup-manual" },
      h("summary", {}, t("setup.manual")),
      h("div", { class: "setup-row" },
        h("div", { class: "setup-text" },
          h("div", { class: "setup-step" }, t("setup.audio_manual"), " ",
            h("a", { href: "https://github.com/0xShug0/audio.cpp/releases/latest", target: "_blank", rel: "noreferrer" }, "audio.cpp"), " / ",
            h("a", { href: "https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/main/Irodori-TTS-v4-Small-GGUF", target: "_blank", rel: "noreferrer" }, "Irodori-TTS GGUF"))),
        h("button", { class: "btn small", onclick: () => emit("open-settings") }, t("ui.open_settings")),
        retry)));
}

function render() {
  const box = $("#welcome");
  const speechMissing = S.engine.state === "error" && S.engine.missing;
  if (!speechMissing && S.llm.ok && !(result && !result.ok)) { box.replaceChildren(); return; }
  if (speechMissing) { box.replaceChildren(S.engine.missing.startsWith("audio") ? audioCard() : speechCard()); return; }
  box.replaceChildren(h("div", { class: "setup" },
    h("h2", {}, needsLogin() ? t("setup.login_title", { name: S.llm.auth.name }) : t("setup.llm_title")),
    h("p", { class: "setup-lead" }, needsLogin()
      ? t("setup.login_lead")
      : t("setup.llm_lead")),
    needsLogin() ? loginBlock() : null,
    ...CHOICES.map((c) => {
      const btn = h("button", { class: "btn small", onclick: () => use(c, btn) }, t("setup.use"));
      const note = result?.id === c.id ? h("div", { class: `test-result ${result.ok ? "ok" : "ng"}` }, result.text) : null;
      return h("div", { class: "setup-row" },
        h("div", { class: "setup-text" },
          h("div", { class: "setup-name" }, c.name, S.config.llm_provider === c.id ? h("span", { class: "setup-now" }, t("setup.selected")) : null),
          h("div", { class: "setup-step" }, t(`setup.${c.id}`), " ", h("a", { href: c.url, target: "_blank", rel: "noreferrer" }, t("setup.steps"))),
          note),
        btn);
    })));
}

export function initSetup() {
  on("llm", () => { if (S.llm.ok && result && !result.ok) result = null; render(); });
  on("config-changed", render);
  on("lang", render);
  on("engine", render);
  on("setup", render);
  render();
}
