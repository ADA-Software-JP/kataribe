import { t } from "./i18n.js";
import { S, api, emit, toast } from "./state.js";

export const lines = () => S.live.log.filter((e) => e.kind === "line");
export const playing = () => lines().find((l) => l.state === "playing");
export const hasContent = () => S.live.log.some((e) => e.kind === "user" || e.kind === "line");
export const byId = (id) => S.live.log.find((e) => e.id === id);

export function applySnapshot(live) {
  S.live = live;
  emit("live");
}

export function applyStatus(status) {
  Object.assign(S.live, status);
  emit("live");
}

export function addEntry(entry) {
  if (byId(entry.id)) return;
  // 声を変えて作り直す行は、元の行のすぐ後ろに入る(after)
  const at = entry.after ? S.live.log.findIndex((e) => e.id === entry.after) : -1;
  if (at >= 0) S.live.log.splice(at + 1, 0, entry);
  else S.live.log.push(entry);
  emit("live-entry", entry);
}

export function patchLine(patch) {
  const line = byId(patch.id);
  if (!line) return;
  Object.assign(line, patch);
  emit("live-line", line);
}

export function dropLines(ids) {
  for (const id of ids) {
    const line = byId(id);
    if (line) line.state = "dropped";
  }
  emit("live-drop", ids);
}

export function clearLog() {
  S.live.log = [];
  emit("live-reset");
}

/** 行の音声(WAV と JSON)を保存先に残す / 外す(★)。残した時は、保存先を示してフォルダーを開けるようにする。 */
export async function toggleStar(line) {
  const value = !line.starred;
  try {
    const r = await api("POST", "/api/live/star", { id: line.id, value });
    patchLine({ id: line.id, starred: r.starred, stem: r.stem });
    if (r.starred) {
      toast(t("keep.kept_toast", { dir: r.dir, stem: r.stem }), { action: t("keep.open_folder"), ms: 12000,
        onAction: () => api("POST", "/api/open", { which: "output" }).catch((e) => toast(e.message, { kind: "err" })) });
    } else {
      toast(t("keep.removed"));
    }
  } catch (e) { toast(e.message, { kind: "err" }); }
}
