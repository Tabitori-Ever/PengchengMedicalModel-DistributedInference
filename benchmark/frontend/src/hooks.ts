import { useEffect, useRef } from 'react';

/**
 * 轮询：标签页隐藏时暂停（省流且避免后台堆积），重新可见时立即拉一次。
 * fn 通过 ref 转发，因此调用方不必用 useCallback 包裹。
 */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, enabled = true) {
  const ref = useRef(fn);
  ref.current = fn;

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: number | undefined;

    const schedule = () => {
      if (stopped) return;
      timer = window.setTimeout(tick, intervalMs);
    };
    const tick = async () => {
      if (stopped) return;
      if (typeof document !== 'undefined' && document.hidden) {
        schedule();
        return;
      }
      try {
        await ref.current();
      } finally {
        schedule();
      }
    };
    const onVisible = () => {
      if (typeof document !== 'undefined' && !document.hidden) {
        window.clearTimeout(timer);
        void tick();
      }
    };

    void tick();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs, enabled]);
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
