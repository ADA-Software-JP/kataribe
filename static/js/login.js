import { iconEl } from "./icons.js";
import { t } from "./i18n.js";
import { S, api, emit, h, on, toast } from "./state.js";

/* Claude Code / Codex のログイン。押すとサーバーが login コマンドを起動し、ブラウザでの認証が通るまで状態を調べ続ける。
   表示は SSE の llm イベントで切り替わる(S.llm.auth)。 */

export const authOf = () => S.llm.auth || { state: "none" };
/** 選んでいる LLM がログインを求めている(未ログイン、期限切れ、ログイン中)。 */
export const needsLogin = () => ["out", "login"].includes(authOf().state);

async function call(path) {
  try {
    S.llm = await api("POST", path, { provider: S.llm.provider });
    emit("llm");
  } catch (e) {
    toast(e.message, { kind: "err" });
  }
}

export const startLogin = () => call("/api/llm/login");
export const cancelLogin = () => call("/api/llm/login/cancel");
export const recheckLogin = () => call("/api/llm/auth");

const manual = (a) => h("div", { class: "help" }, t("login.manual_pre"), " ", h("code", {}, a.command), " ", t("login.manual_post"));

const fill = (box, ...kids) => box.replaceChildren(...kids.filter(Boolean));

function paint(box) {
  const a = authOf();
  box.classList.toggle("hidden", !needsLogin());
  if (!needsLogin()) { box.replaceChildren(); return; }
  if (a.state === "login") {
    fill(box,
      h("div", { class: "login-head" }, iconEl("loader-circle", "spin"),
        h("span", {}, t("login.waiting", { name: a.name, min: Math.round(a.timeout / 60) }))),
      a.url && a.url.startsWith("https://")
        ? h("div", { class: "help" }, t("login.url_pre"), " ", h("a", { href: a.url, target: "_blank", rel: "noreferrer" }, t("login.url_link")), t("login.url_post"))
        : null,
      a.hint && !a.url ? h("div", { class: "help" }, t("login.hint")) : null,
      manual(a),
      h("div", { class: "set-row" }, h("button", { class: "btn small", onclick: cancelLogin }, t("ui.cancel"))));
    return;
  }
  fill(box,
    h("div", { class: "login-head" }, h("strong", {}, t("login.out", { name: a.name }))),
    a.error ? h("div", { class: "test-result ng" }, a.error) : null,
    h("div", { class: "set-row" },
      h("button", { class: "btn primary", onclick: startLogin }, t("login.sign_in", { name: a.name })),
      h("button", { class: "btn small", onclick: recheckLogin }, t("login.recheck"))),
    manual(a));
}

/** ログインの案内の枠。ログインが要らない間は空で隠れる。 */
export function loginBlock() {
  const box = h("div", { class: "login-box hidden" });
  box._paint = () => paint(box);
  paint(box);
  return box;
}

on("llm", () => document.querySelectorAll(".login-box").forEach((box) => box._paint?.()));
