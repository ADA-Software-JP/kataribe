/* 画面の文言。辞書は static/i18n/ の言語ごとの .json(サーバー側の文言と同じ辞書)。
   キーが現在の言語に無い時は英語、英語にも無ければキーそのものを出す。言語は設定(config.language)で選ぶ。
   設定が auto の時の言語はサーバーが OS から決め、/api/state の lang で返す(画面はそれに従う)。 */
export const LANGS = [["en", "English"], ["ja", "日本語"], ["zh-Hans", "简体中文"], ["zh-Hant", "繁體中文"]];
const dict = Object.fromEntries(LANGS.map(([code]) => [code, {}]));
let lang = "en";

export async function loadDicts() {
  await Promise.all(LANGS.map(async ([code]) => {
    try { dict[code] = await (await fetch(`/static/i18n/${code}.json`)).json(); } catch { dict[code] = {}; }
  }));
}

export const getLang = () => lang;

export function setLang(code) {
  lang = dict[code] ? code : "en";
  document.documentElement.lang = lang;
}

/** key の文言。{名前} を vars で置き換える(辞書に無い名前はそのまま残す)。 */
export function t(key, vars = {}) {
  let text = dict[lang][key] ?? dict.en[key] ?? key;
  for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(String(value));
  return text;
}

/** 文の途中に要素(リンクなど)を挟む文言。{名前} の所に nodes[名前] を入れた、文字と要素の並びを返す。 */
export function tn(key, nodes, vars = {}) {
  const parts = t(key, vars).split(/(\{\w+\})/);
  return parts.map((part) => {
    const m = /^\{(\w+)\}$/.exec(part);
    return m && nodes[m[1]] !== undefined ? nodes[m[1]] : part;
  }).filter((part) => part !== "");
}

/** 数の表示を言語に合わせる(桁区切り、小数点)。 */
export const num = (value, digits = 2) => new Intl.NumberFormat(lang, { maximumFractionDigits: digits }).format(value);

/** 日付と時刻の表示を言語に合わせる。 */
export const date = (value) => new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(value);

/** index.html の data-i18n(文字)、data-i18n-title、data-i18n-aria、data-i18n-placeholder を、今の言語で入れ直す。 */
export function applyStatic() {
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const [attr, name] of [["i18nTitle", "title"], ["i18nAria", "aria-label"], ["i18nPlaceholder", "placeholder"]]) {
    for (const el of document.querySelectorAll(`[data-${attr.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`)) el.setAttribute(name, t(el.dataset[attr]));
  }
}
