import { iconEl, svg } from "./icons.js";
import { t } from "./i18n.js";
import { hasContent } from "./live.js";
import { unlock } from "./player.js";
import { $, S, api, emit, h, on, setText, toast } from "./state.js";

/* 入力欄の上の操作: 一時停止、新しく始める、声、声の登録。声の一覧は声の名前を押すか、「/」「チェンジ」と打つと出る。 */
const PICK = /^\s*(?:\/|チェンジ|change(?=\s*(?:[,.!?、。！？]|$)))/i;
let panel = "";   // "" | "pick" | "save" | "restart"

// 内蔵の声(Qwen3-TTS など、声を選ぶモデル)と、登録した声(声のファイルを使えるモデルだけ)
const presets = () => S.engine.model?.presets || [];
const fileVoices = () => (S.engine.model?.voice_files === false ? [] : S.voices);

// 登録した声の一覧。名前の行を押すとその声を使い、ごみ箱を押すと、その行の横に確認が出る
// fresh: 一覧の先頭に置く「新しい声」の行 { current, onclick }。内蔵の声を使うモデルでは出さない
function savedVoices(now, use, fresh = null) {
  const rows = fileVoices();
  if (!rows.length && !fresh) return [];
  return [h("div", { class: "voice-list" },
    h("div", { class: "voice-list-body" }, ...(fresh ? [h("div", { class: "voice-row" },
      h("button", { type: "button", class: "voice-use", "aria-current": String(fresh.current), onclick: fresh.onclick },
        iconEl("speech"), h("span", {}, t("ctl.new_voice")), ...(fresh.current ? [iconEl("check", "voice-on")] : [])),
      h("span", { class: "voice-del-space" }))] : []), ...rows.map((v) => {
      const row = h("div", { class: "voice-row" },
        h("button", { type: "button", class: "voice-use", "aria-current": String(v.name === now), onclick: use(v.name) },
          iconEl("audio-lines"), h("span", {}, v.name), ...(v.name === now ? [iconEl("check", "voice-on")] : [])),
        h("button", { type: "button", class: "voice-del", title: t("set.delete"), "aria-label": `${t("set.delete")}: ${v.name}`, onclick: () => askDelete(row, v.name) }, iconEl("trash-2")));
      return row;
    }))),
  ];
}

// 削除の確認は、ごみ箱を押した行の右に吹き出しで出す(一覧のスクロールに切られないよう body に置く)
let ask = null;
function closeAsk() {
  ask?.remove();
  ask = null;
  document.querySelector(".voice-row.asking")?.classList.remove("asking");
}
function askDelete(row, name) {
  const again = row.classList.contains("asking");
  closeAsk();
  if (again) return;
  row.classList.add("asking");
  ask = h("div", { class: "voice-ask", role: "dialog", "aria-label": t("ctl.delete_ask") },
    h("div", { class: "voice-ask-text" }, t("ctl.delete_ask")),
    h("div", { class: "voice-ask-btns" },
      h("button", { type: "button", onclick: closeAsk }, t("ui.cancel")),
      h("button", { type: "button", class: "danger", onclick: async () => {
        try {
          S.voices = (await api("POST", "/api/voices/delete", { name })).voices;
          closeAsk();
          emit("voices");
          renderPanel();
        } catch (err) { toast(err.message, { kind: "err" }); }
      } }, t("set.delete"))));
  document.body.append(ask);
  const r = row.getBoundingClientRect();
  const box = $("#voicePick").getBoundingClientRect();
  const w = ask.offsetWidth;
  const right = box.right + 16 + w <= innerWidth - 12;   // 右に入らない時は左に出す
  ask.dataset.side = right ? "right" : "left";
  ask.style.left = `${Math.max(12, right ? box.right + 16 : box.left - 16 - w)}px`;
  ask.style.top = `${Math.max(12, r.top + r.height / 2 - ask.offsetHeight / 2)}px`;
}

/** 内蔵の声を選ぶボタン。選ぶと設定の「声」に入り、次の 1 本からその声になる。 */
function presetButtons() {
  const now = S.live.voice_origin === "preset" ? S.live.voice : "";
  return presets().map((v) => h("button", { type: "button", "aria-current": String(v.name === now), onclick: async () => {
    try {
      S.config = await api("PUT", "/api/config", { tts_voice: v.name });
      close();
      renderControls();
    } catch (e) { toast(e.message, { kind: "err" }); }
  } }, v.name));
}

function voiceName() {
  const v = S.live;
  if (v.voice_state === "fixing") return t("ctl.fixing");
  // 始める前: 設定の「既定の声」があればその文、無ければ話し始めてからと同じ「新しい声」
  if (v.voice_state === "pending" && !hasContent()) return S.config.default_how || t("ctl.new_voice");
  if (v.voice_state === "pending") return t("ctl.next_voice");
  if (v.voice_origin === "saved" || v.voice_origin === "preset") return v.voice;
  return v.voice_saved || t(v.voice_origin === "new" ? "ctl.new_voice" : "ctl.first_voice");
}

const canSave = () => S.live.voice_state === "fixed" && !["saved", "preset"].includes(S.live.voice_origin) && !S.live.voice_saved
  && S.engine.model?.voice_files !== false;

function setIcon(btn, name, label) {
  if (btn.dataset.icon === name && btn.title === label) return;
  btn.dataset.icon = name;
  btn.querySelector(".i").innerHTML = svg(name);
  btn.title = label;
  btn.setAttribute("aria-label", label);
}

async function send(text) {
  unlock();
  try { await api("POST", "/api/live/send", { text }); } catch (e) { toast(e.message, { kind: "err" }); }
}

/** 始める前の声の選択: 文章で決める(設定の「既定の声」に入る)、新しい声(LLM に任せる)、登録した声。 */
function firstPick(box, now, focus) {
  const setHow = async (text) => {
    try {
      S.config = await api("PUT", "/api/config", { default_how: text, default_voice: "" });
      close();
      renderControls();
    } catch (e) { toast(e.message, { kind: "err" }); }
  };
  const desc = h("input", { class: "field-input", value: S.config.default_how || "", maxlength: "200", spellcheck: "false", autocomplete: "off",
    placeholder: t("set.default_how_ph"), "aria-label": t("set.default_how") });
  desc.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); setHow(desc.value.trim()); }
    if (e.key === "Escape") { e.stopPropagation(); close(); }
  });
  const use = (name) => async () => {
    try {
      if (S.config.default_how) S.config = await api("PUT", "/api/config", { default_how: "" });
      await api("POST", "/api/live/use_voice", { name });
      close();
    } catch (e) { toast(e.message, { kind: "err" }); }
  };
  // 参照デザインどおり、登録した声の一覧を先に出し、文章で決める欄と「新しい声」はその下にまとめる
  box.replaceChildren(
    ...savedVoices(now, use, presets().length ? null : { current: !now && !S.config.default_how, onclick: () => setHow("") }),
    ...presetButtons(),
    h("div", { class: "pick-desc" }, desc, h("button", { type: "button", class: "solid", onclick: () => setHow(desc.value.trim()) }, t("ctl.set_voice"))),
  );
  fitPick();
  if (focus) desc.focus();
}

// 声の一覧は、声の名前の上に開く。画面に固定して置くので、スクロールする欄に切られない。
// 上の空き(見出しの下から)が中身より狭い時は下に開き、それでも足りない分は一覧の中をスクロールする。
function fitPick() {
  const box = $("#voicePick");
  if (box.classList.contains("hidden")) return;
  const chip = $("#voiceChip");
  const rect = chip.getBoundingClientRect();
  const top = (document.querySelector("header.top")?.getBoundingClientRect().bottom || 0) + 12;
  const gap = 12;
  const above = rect.top - gap - top;
  const below = innerHeight - rect.bottom - gap - 12;
  box.style.maxHeight = "none";
  const need = box.scrollHeight;
  const side = need <= above || above >= below ? "above" : "below";
  box.dataset.side = chip.dataset.side = side;
  box.style.left = `${rect.left + rect.width / 2}px`;
  box.style.top = side === "above" ? "auto" : `${rect.bottom + gap}px`;
  box.style.bottom = side === "above" ? `${innerHeight - rect.top + gap}px` : "auto";
  box.style.maxHeight = `${Math.max(120, side === "above" ? above : below)}px`;
}
window.addEventListener("resize", fitPick);
document.addEventListener("scroll", fitPick, true);

function renderPanel() {
  const box = $("#voicePick");
  const typed = PICK.test($("#input").value);
  const mode = panel || (typed ? "pick" : "");
  box.classList.toggle("hidden", !mode);
  $("#voiceChip").setAttribute("aria-expanded", String(mode === "pick"));
  if (box.dataset.mode === mode && mode !== "pick") return;
  box.dataset.mode = mode;
  if (mode === "pick") {
    const choose = (name) => () => {
      if (typed) $("#input").value = "";
      close();
      send(name ? `/change ${name}` : "/change");
      $("#input").focus();
    };
    const now = S.live.voice_origin === "saved" ? S.live.voice : (S.live.voice_saved || "");
    if (!hasContent()) return firstPick(box, now, !typed);
    box.replaceChildren(
      ...savedVoices(now, choose, presets().length ? null : { current: false, onclick: choose("") }),
      ...presetButtons(),
    );
  } else if (mode === "save") {
    const name = h("input", { class: "field-input", value: `voice-${S.voices.length + 1}`, spellcheck: "false", autocomplete: "off", "aria-label": t("ctl.voice_name") });
    const save = async () => {
      try {
        const r = await api("POST", "/api/live/save_voice", { name: name.value.trim() });
        S.voices = r.voices;
        emit("voices");
        toast(t("ctl.saved_toast", { name: r.name }));
        close();
      } catch (e) { toast(e.message, { kind: "err" }); }
    };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); save(); } if (e.key === "Escape") { e.stopPropagation(); close(); } });
    box.replaceChildren(name, h("button", { type: "button", class: "solid", onclick: save }, t("ctl.save")));
    name.focus();
    name.select();
  } else if (mode === "restart") {
    // 会話を消すので、押した時に一度だけ確かめる
    const restart = async () => {
      close();
      try { await api("POST", "/api/live/reset", {}); } catch (e) { toast(e.message, { kind: "err" }); }
      $("#input").focus();
    };
    box.replaceChildren(h("span", { class: "pick-note" }, t("ctl.restart_ask")),
      h("button", { type: "button", class: "solid", onclick: restart }, t("ui.start_over")),
      h("button", { type: "button", onclick: close }, t("ui.cancel")));
    box.querySelector(".solid").focus();
  } else {
    box.replaceChildren();
  }
  fitPick();
}

function open(mode) {
  panel = panel === mode ? "" : mode;
  renderPanel();
}

function close() {
  closeAsk();
  panel = "";
  renderPanel();
}

/** ショートカットから: 声の一覧と、新しく始める確認を開く(新しく始めるは、最初の画面では何もしない) */
export const openVoicePick = () => open("pick");
export const askRestart = () => { if (hasContent()) open("restart"); };

export function renderControls() {
  const live = hasContent();
  // 最初の画面では声の選択だけを出す(始める前に声を決められるように)。一時停止・新しく始める・声を登録は始めてから
  $("#controls").dataset.first = live ? "0" : "1";
  setText($("#voiceChip .vname"), voiceName());
  if (!live) { if (panel && panel !== "pick") close(); return; }
  const running = S.live.running;
  setIcon($("#pauseBtn"), running ? "pause" : "play", running ? t("ctl.pause") : t("ctl.resume"));
  setText($("#voiceChip .vname"), voiceName());
  const saved = S.live.voice_origin === "saved" || Boolean(S.live.voice_saved);
  const save = $("#saveBtn");
  setIcon(save, saved ? "bookmark-check" : "bookmark", saved ? t("ctl.saved_title") : t("ctl.save_title"));
  setText(save.querySelector(".ctl-label"), t(saved ? "ctl.saved_label" : "ui.save_voice"));
  save.disabled = !canSave();
  // 録音から声を作れないモデル(内蔵の声だけで話す)では、声を登録するボタンを出さない
  save.classList.toggle("hidden", S.engine.model?.voice_files === false);
  if (panel === "save" && !canSave()) close();
}

/** 入力欄の文字が変わった時。「/」「チェンジ」で声の一覧を出し、消したら閉じる。 */
export const onInputChange = renderPanel;

export function initControls() {
  $("#pauseBtn").addEventListener("click", () => emit("toggle-running"));
  $("#restartBtn").addEventListener("click", () => open("restart"));
  $("#voiceChip").addEventListener("click", () => open("pick"));
  $("#saveBtn").addEventListener("click", () => open("save"));
  // 一覧は画面に固定して出すので body の直下に置く(親の transform や overflow に位置と表示を左右されない)
  document.body.append($("#voicePick"));
  document.addEventListener("pointerdown", (e) => { if (panel && !e.target.closest("#controls, #voicePick, .voice-ask")) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel) { e.stopImmediatePropagation(); close(); } }, true);
  for (const ev of ["live", "live-entry", "live-line", "live-reset", "voices", "tab"]) on(ev, renderControls);
  renderControls();
}
