import { useEffect, useRef } from 'react';

/** Run cb every `ms` while active; skipped while the tab is hidden
 *  (avoids background tabs/devices hammering the single scheduler). */
export function useTicker(cb: () => void, ms: number, active = true) {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      ref.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
}

export function useMounted() {
  const mounted = useRef(true);
  useEffect(() => () => {
    mounted.current = false;
  }, []);
  return mounted;
}

export function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
