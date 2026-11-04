import { iconEl } from "./icons.js";
import { getLang, t } from "./i18n.js";
import { $, S, api, h, on, toast } from "./state.js";

/* これまでの会話(左のパネル)。開くと、その会話を止めた状態で読み込む(続きは ▶ か一言で書かせる)。 */

let items = [];

export const historyOpen = () => !$("#historyPanel").classList.contains("hidden");

function when(sec) {
  const d = new Date(sec * 1000);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(getLang(), { month: "short", day: "numeric" });
}

function render() {
  const list = $("#historyList");
  if (!items.length) {
    list.replaceChildren(h("p", { class: "hist-empty" }, t("hist.empty")));
    return;
  }
  list.replaceChildren(...items.map((it) => {
    const del = h("button", { class: "hist-del", type: "button", title: t("set.delete"), "aria-label": t("set.delete") }, iconEl("trash-2"));
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      // 2 回押しで消す(1 回目で「本当に削除」に変わる)
      if (!del.dataset.sure) {
        del.dataset.sure = "1";
        del.replaceChildren(t("set.delete_sure"));
        setTimeout(() => { if (del.isConnected) { delete del.dataset.sure; del.replaceChildren(iconEl("trash-2")); } }, 4000);
        return;
      }
      try { items = (await api("POST", "/api/history/delete", { id: it.id })).items; render(); } catch (err) { toast(err.message, { kind: "err" }); }
    });
    const row = h("div", { class: "hist-item", role: "button", tabindex: "0", "aria-current": String(it.id === S.live.hid) },
      h("span", { class: "hist-title" }, it.title || "…"),
      h("span", { class: "hist-meta" }, `${when(it.updated)} · ${t("hist.lines", { n: it.lines })}`),
      del);
    const open = async () => {
      if (it.id === S.live.hid) { closeHistory(); return; }
      try { await api("POST", "/api/history/open", { id: it.id }); closeHistory(); } catch (err) { toast(err.message, { kind: "err" }); }
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(); } });
    return row;
  }));
}

async function refresh() {
  try { items = (await api("GET", "/api/history")).items; } catch { return; }
  render();
}

export function openHistory() {
  $("#historyPanel").classList.remove("hidden");
  $("#historyBtn").setAttribute("aria-expanded", "true");
  refresh();
}

export function closeHistory() {
  $("#historyPanel").classList.add("hidden");
  $("#historyBtn").setAttribute("aria-expanded", "false");
}

export function initHistory() {
  $("#historyBtn").addEventListener("click", () => (historyOpen() ? closeHistory() : openHistory()));
  $("#historyNew").addEventListener("click", async () => {
    closeHistory();
    try { await api("POST", "/api/live/reset", {}); } catch (e) { toast(e.message, { kind: "err" }); }
    $("#input").focus();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && historyOpen()) { e.stopImmediatePropagation(); closeHistory(); } }, true);
  document.addEventListener("pointerdown", (e) => {
    if (historyOpen() && !e.target.closest("#historyPanel, #historyBtn")) closeHistory();
  });
  // 会話が書き足されたら、開いている間だけ一覧を読み直す
  let timer = 0;
  on("history", () => { if (historyOpen()) { clearTimeout(timer); timer = setTimeout(refresh, 300); } });
  on("live", () => { if (historyOpen()) render(); });
}
