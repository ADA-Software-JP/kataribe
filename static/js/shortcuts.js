import { t } from "./i18n.js";
import { S } from "./state.js";

/* ショートカット。操作ごとのキーを config.json の shortcuts に持つ(無い項目は既定、"" は割り当てなし)。
   キーは "Ctrl+Alt+Shift+Meta+キー" の形の文字列で持つ。 */

/** [操作, 既定のキー, 名前の辞書キー]。順番が設定と一覧の並び。 */
export const ACTIONS = [
  ["send", "Enter", "set.sc.send"],
  ["newline", "Shift+Enter", "set.sc.newline"],
  ["pause", "Esc", "set.sc.pause"],
  ["toggle", "Ctrl+Space", "set.sc.toggle"],
  ["help", "?", "set.sc.help"],
  ["restart", "", "r21.sc.restart"],
  ["voice", "", "r21.sc.voice"],
  ["settings", "Ctrl+,", "r21.sc.settings"],
  ["mic_start", "Alt+M", "r21.sc.mic_start"],
  ["mic_commit", "Ctrl+Enter", "r21.sc.mic_commit"],
  ["mic_cancel", "Esc", "r21.sc.mic_cancel"],
];

// 同じキーでも別々の場面で働くので、重複とは数えない組(録音中の「取り消し」は、録音していない時の「一時停止」と同じ Esc)
const SHARED = [["pause", "mic_cancel"]];

const DEFAULTS = Object.fromEntries(ACTIONS.map(([id, key]) => [id, key]));
let map = { ...DEFAULTS };

export const defaultKey = (id) => DEFAULTS[id];
export const keyOf = (id) => map[id];

/** 保存済みの設定から読み込む(知らない操作や文字でない値は捨てる)。 */
export function loadShortcuts(saved) {
  map = { ...DEFAULTS };
  for (const [id, key] of Object.entries(saved || {})) if (id in DEFAULTS && typeof key === "string") map[id] = key;
}

export function setKey(id, key) {
  map = { ...map, [id]: key };
  return { ...map };
}

const MODIFIER_KEYS = new Set(["Control", "Shift", "Alt", "Meta", "AltGraph", "OS"]);
const NAMES = { Escape: "Esc", " ": "Space", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right" };

export const isModifierOnly = (e) => MODIFIER_KEYS.has(e.key);

/** "Ctrl+Shift+M" を、修飾キーとキーに分ける(キー自体が "+" の時も扱う)。 */
function partsOf(combo) {
  const out = [];
  let rest = combo;
  for (let m = /^(Ctrl|Alt|Shift|Meta)\+(?=.)/.exec(rest); m; m = /^(Ctrl|Alt|Shift|Meta)\+(?=.)/.exec(rest)) {
    out.push(m[1]);
    rest = rest.slice(m[0].length);
  }
  return [...out, rest];
}

/** キーイベントを "Ctrl+Shift+M" の形に直す。修飾キーだけの時は ""。 */
export function comboOf(e) {
  if (isModifierOnly(e)) return "";
  const withMod = e.ctrlKey || e.altKey || e.metaKey;
  const code = /^Key([A-Z])$/.exec(e.code || "") || /^Digit(\d)$/.exec(e.code || "");
  let key = withMod && code ? code[1] : e.key;
  key = NAMES[key] || key;
  if (key.length === 1) key = key.toUpperCase();
  // 記号は Shift を押した結果の文字なので、Shift は別に数えない("?" は Shift + / だが、"?" と書く)
  const shift = e.shiftKey && !(key.length === 1 && !/[A-Z0-9]/.test(key));
  return [e.ctrlKey && "Ctrl", e.altKey && "Alt", shift && "Shift", e.metaKey && "Meta", key].filter(Boolean).join("+");
}

/** 画面に出すキー。Meta は macOS では Cmd、それ以外は Win。 */
export const showKey = (combo) => (combo ? partsOf(combo).map((p) => (p === "Meta" ? (S.platform === "mac" ? "Cmd" : "Win") : p)) : []);

/** 打つと文字になるだけのキー(修飾キー無し)。入力欄の中では働かせない。 */
export function isPlainPrintable(combo) {
  const parts = partsOf(combo);
  const key = parts[parts.length - 1];
  return !parts.some((p) => ["Ctrl", "Alt", "Meta"].includes(p)) && (key.length === 1 || key === "Space");
}

/** そのキーに割り当てている操作の一覧(重複していれば複数)。 */
export const actionsFor = (combo) => (combo ? ACTIONS.map(([id]) => id).filter((id) => map[id] === combo) : []);

/** id と同じキーを使っている、ほかの操作。 */
export function conflictsOf(id) {
  const key = map[id];
  if (!key) return [];
  return ACTIONS.map(([other]) => other).filter((other) => other !== id && map[other] === key
    && !SHARED.some(([a, b]) => (a === id && b === other) || (a === other && b === id)));
}

export const labelOf = (id) => t(ACTIONS.find(([a]) => a === id)[2]);
