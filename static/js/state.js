
import { t } from "./i18n.js";

export const S = {
  engine: { state: "stopped", error: "", since: 0, voices: [] },
  llm: { provider: "claude", state: "stopped", ok: true, reason: "", label: "Claude Haiku 5.5", auth: { state: "none" } },
  live: { running: true, voice: "none", voice_state: "pending", voice_origin: "first", voice_saved: "", how: "",
    rewriting: false, making: false, steer: "", agent: { state: "idle", n: 0, error: "" }, tts_error: "", log: [] },
  voices: [],
  config: {},
  promptDefaults: {},
  promptVars: {},
  promptLimits: {},
  modelChoices: {},
  defaultModels: {},
  efforts: [],
  tuningSpec: [],
  platform: "windows",
  osLang: "en",   // サーバーが OS から決めた言語(設定が auto の時に使う)
};

const listeners = new Map();
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, []);
  listeners.get(event).push(fn);
}
export function emit(event, data) {
  for (const fn of listeners.get(event) || []) fn(data);
}

export async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* 本文なし */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

export function toast(message, { kind = "", action = "", onAction = null, ms = 6000 } = {}) {
  const box = $("#toasts");
  const el = h("div", { class: `toast ${kind}`, role: "status" }, h("span", {}, message));
  const close = () => { el.remove(); };
  if (action) {
    el.append(h("button", { class: "btn small", onclick: () => { close(); onAction?.(); } }, action));
  }
  el.append(h("button", { class: "btn small", onclick: close }, t("ui.close")));
  box.replaceChildren(el);
  setTimeout(close, ms);
}
