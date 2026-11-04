/* 画面更新の動き。opacity と transform だけを動かす。 */
const DUR = 220;
const EASE = "cubic-bezier(0.2, 0.7, 0.2, 1)";
const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 新しい要素を、下から薄く現す。 */
export function enter(el) {
  if (!calm()) el.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: DUR, easing: EASE });
}

/** 要素を消す。場所を空けて(絶対配置にして)他の要素が詰められるようにし、薄くしてから外す。 */
export function leave(el) {
  const parent = el.parentElement;
  if (calm() || !parent) { el.remove(); return; }
  const box = parent.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  Object.assign(el.style, { position: "absolute", top: `${r.top - box.top + parent.scrollTop}px`, left: `${r.left - box.left}px`, width: `${r.width}px`, pointerEvents: "none" });
  el.classList.add("leaving");
  el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateX(-12px)" }], { duration: DUR, easing: EASE, fill: "forwards" }).onfinish = () => el.remove();
}

/** change() で並びが変わっても、残る要素は元の位置から滑らかに動く(FLIP)。 */
export function flip(container, change, dur = DUR) {
  const keyed = () => [...container.children].filter((c) => c.dataset.id && !c.classList.contains("leaving"));
  const before = new Map(keyed().map((c) => [c, c.getBoundingClientRect().top]));
  change();
  if (calm()) return;
  for (const c of keyed()) {
    const was = before.get(c);
    if (was === undefined) continue;
    const dy = was - c.getBoundingClientRect().top;
    if (Math.abs(dy) > 1) c.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: dur, easing: EASE });
  }
}

/** 文字の差し替え。古い文字と重ねると二重に見えるので、1 つの要素の文字を替えて軽く浮かび上がらせるだけにする。 */
export function crossfade(slot, text) {
  let cur = slot.firstElementChild;
  if (!cur) { cur = document.createElement("span"); slot.append(cur); }
  if (cur.textContent === text) return;
  const quiet = cur.textContent.replace(/\d+/g, "") === text.replace(/\d+/g, "");
  cur.textContent = text;
  if (!calm() && !quiet && text) cur.animate([{ opacity: 0.35 }, { opacity: 1 }], { duration: DUR, easing: "ease-out" });
}
