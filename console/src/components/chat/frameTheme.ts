import { useEffect, type RefObject } from 'react';

/** The host page's light/dark mode (`data-color-mode` on <html>), if set. */
export function hostColorMode(): 'light' | 'dark' | undefined {
  if (typeof document === 'undefined') return undefined;
  const mode = document.documentElement.dataset.colorMode;
  return mode === 'light' || mode === 'dark' ? mode : undefined;
}

/** Keep a rendered component's page in the host's light/dark mode when it
 * changes after the frame loaded — by message, so the component isn't
 * reloaded (and doesn't lose its state). The page applies only "light" or
 * "dark" from its parent; nothing else crosses. */
export function useFrameTheme(frame: RefObject<HTMLIFrameElement | null>): void {
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const send = () => {
      const theme = hostColorMode();
      if (theme && frame.current?.contentWindow) {
        frame.current.contentWindow.postMessage({ type: 'sinas:theme', theme }, '*');
      }
    };
    const observer = new MutationObserver(send);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-color-mode'],
    });
    // A switch while the frame was still loading (its URL carries the old
    // mode, its listener wasn't there yet): send the current mode on load.
    const element = frame.current;
    element?.addEventListener('load', send);
    return () => {
      observer.disconnect();
      element?.removeEventListener('load', send);
    };
  }, [frame]);
}
