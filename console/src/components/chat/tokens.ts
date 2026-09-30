/**
 * CSS custom property tokens for theming.
 * Components read these at runtime via var().
 *
 * Vendored from the retired @sinas/ui package. In the console, the
 * --sinas-* variables are defined in index.css mapped onto the console's
 * theme tokens (so they follow light/dark automatically); the `defaults`
 * below survive only as var() fallbacks for portability.
 */

export const tokens = {
  // Brand
  colorPrimary: '--sinas-color-primary',
  colorPrimaryHover: '--sinas-color-primary-hover',
  colorPrimaryMuted: '--sinas-color-primary-muted',   // Orange with low opacity (glows, focus rings)
  colorDanger: '--sinas-color-danger',
  colorSuccess: '--sinas-color-success',
  colorWarning: '--sinas-color-warning',

  // Text
  colorText: '--sinas-color-text',
  colorTextMuted: '--sinas-color-text-muted',
  colorTextInverse: '--sinas-color-text-inverse',      // Dark text on light bg (e.g. orange buttons)

  // Surfaces
  colorBg: '--sinas-color-bg',
  colorBgSubtle: '--sinas-color-bg-subtle',
  colorBgElevated: '--sinas-color-bg-elevated',        // Cards / elevated panels

  // Glass
  colorGlass: '--sinas-color-glass',                   // Semi-transparent panel bg
  colorGlassBorder: '--sinas-color-glass-border',      // Subtle white border
  colorGlassHover: '--sinas-color-glass-hover',        // Hovered glass surface
  glassBlur: '--sinas-glass-blur',                     // Backdrop blur radius

  // Borders
  colorBorder: '--sinas-color-border',
  colorBorderFocus: '--sinas-color-border-focus',

  // Radii
  radiusSm: '--sinas-radius-sm',
  radiusMd: '--sinas-radius-md',
  radiusLg: '--sinas-radius-lg',

  // Typography
  fontSans: '--sinas-font-sans',
  fontMono: '--sinas-font-mono',

  // Shadows
  shadowGlow: '--sinas-shadow-glow',                   // Orange glow for focus/primary elements
} as const;

export const defaults: Record<string, string> = {
  // Brand
  [tokens.colorPrimary]: '#f97316',
  [tokens.colorPrimaryHover]: '#ea580c',
  [tokens.colorPrimaryMuted]: 'rgba(249, 115, 22, 0.15)',
  [tokens.colorDanger]: '#ef4444',
  [tokens.colorSuccess]: '#22c55e',
  [tokens.colorWarning]: '#eab308',

  // Text
  [tokens.colorText]: '#ededed',
  [tokens.colorTextMuted]: '#888888',
  [tokens.colorTextInverse]: '#111111',

  // Surfaces
  [tokens.colorBg]: '#090909',
  [tokens.colorBgSubtle]: '#111111',
  [tokens.colorBgElevated]: '#161616',

  // Glass
  [tokens.colorGlass]: 'rgba(255, 255, 255, 0.03)',
  [tokens.colorGlassBorder]: 'rgba(255, 255, 255, 0.06)',
  [tokens.colorGlassHover]: 'rgba(255, 255, 255, 0.06)',
  [tokens.glassBlur]: '12px',

  // Borders
  [tokens.colorBorder]: 'rgba(255, 255, 255, 0.08)',
  [tokens.colorBorderFocus]: '#f97316',

  // Radii
  [tokens.radiusSm]: '6px',
  [tokens.radiusMd]: '8px',
  [tokens.radiusLg]: '12px',

  // Typography
  [tokens.fontSans]: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  [tokens.fontMono]: "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace",

  // Shadows
  [tokens.shadowGlow]: '0 0 0 3px rgba(249, 115, 22, 0.15)',
};

/** Helper: read a CSS var with fallback to default */
export function v(token: string): string {
  return `var(${token}, ${defaults[token] || ''})`;
}

/**
 * Inject the chat animation keyframes. Called once on first component render.
 *
 * Unlike the original @sinas/ui version this deliberately does NOT write the
 * --sinas-* variables or any body styling: the console owns both (index.css),
 * and a hardcoded dark :root/body here is exactly what broke light mode.
 */
let baseInjected = false;
export function injectBaseStyles(): void {
  if (baseInjected || typeof document === 'undefined') return;
  baseInjected = true;

  const css = `
@keyframes sinas-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
@keyframes sinas-glow {
  0%, 100% { box-shadow: 0 0 8px rgba(249, 115, 22, 0.2); }
  50% { box-shadow: 0 0 16px rgba(249, 115, 22, 0.35); }
}
@keyframes sinas-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
`;

  const style = document.createElement('style');
  style.id = 'sinas-ui-base';
  style.textContent = css;
  document.head.prepend(style);
}
