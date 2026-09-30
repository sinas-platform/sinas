/** Light/dark theme: a `light` class on <html> flips the CSS variables in
 * index.css. Dark is the default; the choice persists in localStorage.
 * "system" follows prefers-color-scheme live. */

export type Theme = "dark" | "light" | "system";

const STORAGE_KEY = "sinas-theme";
const media = window.matchMedia("(prefers-color-scheme: light)");

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "system" ? stored : "dark";
}

function apply(theme: Theme) {
  const light = theme === "light" || (theme === "system" && media.matches);
  document.documentElement.classList.toggle("light", light);
  // @uiw/react-textarea-code-editor themes itself from the nearest
  // data-color-mode ancestor; setting it here re-themes every code editor.
  document.documentElement.dataset.colorMode = light ? "light" : "dark";
}

export function setTheme(theme: Theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  apply(theme);
}

/** Call once at startup, before first paint. */
export function initTheme() {
  apply(getTheme());
  media.addEventListener("change", () => apply(getTheme()));
}
