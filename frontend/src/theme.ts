export type Scheme = "light" | "dark" | "system";
const storageKey = "inkreference.appearance";
const media = window.matchMedia("(prefers-color-scheme: dark)");
let selected: Scheme = "system";

export function readScheme(): Scheme {
  try {
    const value = localStorage.getItem(storageKey);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return selected;
  }
}
function apply(scheme: Scheme) {
  selected = scheme;
  const theme = scheme === "system" ? media.matches ? "dark" : "light" : scheme;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.scheme = scheme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#101722" : "#f5f7fc");
}
export function setScheme(scheme: Scheme) {
  try { localStorage.setItem(storageKey, scheme); } catch { /* Keep this session usable when storage is unavailable. */ }
  apply(scheme);
  window.dispatchEvent(new Event("inkreference:scheme-changed"));
}
media.addEventListener("change", () => {
  if (selected === "system") apply("system");
});
window.addEventListener("storage", (event) => {
  if (event.key === storageKey || event.key === null) {
    apply(readScheme());
    window.dispatchEvent(new Event("inkreference:scheme-changed"));
  }
});
apply(readScheme());
