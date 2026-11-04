const NAMES = [
  "audio-lines", "bookmark", "bookmark-check", "check", "chevron-down", "folder-open", "loader-circle", "mic", "panel-left", "pause", "play", "plug",
  "rotate-ccw", "send", "settings", "speech", "square", "square-pen", "star", "trash-2", "triangle-alert",
];

const cache = new Map();

await Promise.all(NAMES.map(async (name) => {
  const text = await (await fetch(`/static/vendor/lucide/${name}.svg`)).text();
  cache.set(name, text.replace(/<!--[\s\S]*?-->/, "").replace(/<svg[^>]*>/, (tag) => tag.replace(/\s(width|height)="\d+"/g, "")).trim());
}));

export function svg(name) {
  return cache.get(name) || "";
}

export function iconEl(name, cls = "") {
  const el = document.createElement("i");
  el.className = `i ${cls}`.trim();
  el.innerHTML = svg(name);
  return el;
}

export function hydrate(root = document) {
  for (const el of root.querySelectorAll("i[data-i]")) {
    el.classList.add("i");
    el.innerHTML = svg(el.dataset.i);
    delete el.dataset.i;
  }
}
