import { api, emit } from "./state.js";

/* 声を鳴らすタブは 1 つだけ。最後に開いた(または「このタブで聞く」を押した)タブが担当になる。
   同じブラウザのタブには BroadcastChannel で、別のブラウザやデスクトップ版の窓にはサーバー経由で伝える。 */
const ID = crypto.randomUUID();
let owner = ID;
let channel = null;
const LOCK = "irodori-tab:";

export const isActive = () => owner === ID;

export function claim() {
  const was = owner;
  owner = ID;
  channel?.postMessage({ type: "claim", id: ID });
  api("POST", "/api/player/claim", { id: ID }).catch(() => {});
  if (was !== ID) emit("tab");
}

/** サーバーから: 別の画面が担当になった */
export function onClaim(id) {
  if (id === owner) return;
  owner = id;
  emit("tab");
}

/** ほかに開いているタブがなければ true。タブごとに持つロックで数えるので、裏のタブが眠っていても分かる。 */
export async function alone() {
  if (!navigator.locks) return false;   // 数えられない時は、ほかのタブがあるとみなして会話を消さない
  const { held } = await navigator.locks.query();
  return !held.some((l) => l.name.startsWith(LOCK) && l.name !== LOCK + ID);
}

export function initTab() {
  navigator.locks?.request(LOCK + ID, () => new Promise(() => {}));
  if (typeof BroadcastChannel === "undefined") return;
  channel = new BroadcastChannel("irodori-player");
  channel.onmessage = ({ data }) => {
    if (data.type === "claim" && data.id !== ID) {
      owner = data.id;
      emit("tab");
    } else if (data.type === "bye" && data.id === owner) {
      owner = null;
      emit("tab");
      if (!document.hidden) claim();
    }
  };
  addEventListener("pagehide", () => { if (owner === ID) channel.postMessage({ type: "bye", id: ID }); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && owner === null) claim(); });
  claim();
}
