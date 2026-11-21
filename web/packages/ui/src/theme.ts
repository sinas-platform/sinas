/** Light/dark theme shared with the console: a `light` class on <html>
 * flips the CSS variables in styles.css. Dark is the default. */
export type Theme = 'dark' | 'light' | 'system';

const STORAGE_KEY = 'sinas-theme';
const media = window.matchMedia('(prefers-color-scheme: light)');

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === 'light' || stored === 'system' ? stored : 'dark';
}

export function isLight(theme: Theme = getTheme()): boolean {
  return theme === 'light' || (theme === 'system' && media.matches);
}

function apply(theme: Theme) {
  document.documentElement.classList.toggle('light', isLight(theme));
}

export function setTheme(theme: Theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  apply(theme);
}

/** Call once at startup, before first paint. */
export function initTheme() {
  apply(getTheme());
  media.addEventListener('change', () => apply(getTheme()));
}
