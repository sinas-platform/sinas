import { useCallback, useEffect, type RefObject } from 'react';

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
export function useFrameTheme(frame: RefObject<HTMLIFrameElement | null>): () => void {
  const send = useCallback(() => {
    const theme = hostColorMode();
    if (theme && frame.current?.contentWindow) {
      frame.current.contentWindow.postMessage({ type: 'sinas:theme', theme }, '*');
    }
  }, [frame]);
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const observer = new MutationObserver(send);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-color-mode'],
    });
    return () => observer.disconnect();
  }, [send]);
  // Pass as the frame's onLoad: a switch made while it was still loading
  // (its URL carries the old mode, its listener wasn't there yet).
  return send;
}
}
