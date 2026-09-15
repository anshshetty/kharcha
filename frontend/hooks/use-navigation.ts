'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createNavigation,
  initialNavigation,
  type NavigationState,
} from '@/lib/navigation';

export function useNavigation() {
  const controller = useRef<ReturnType<typeof createNavigation> | null>(null);
  const [state, setState] = useState(initialNavigation);
  const [ready, setReady] = useState(false);
  const [backLabel, setBackLabel] = useState('');
  const pendingScroll = useRef<number | null>(null);
  const publish = useCallback(() => {
    setState(controller.current!.state);
    setBackLabel(controller.current!.backLabel);
  }, []);

  useEffect(() => {
    controller.current = createNavigation(window);
    const restoration = window.history.scrollRestoration;
    window.history.scrollRestoration = 'manual';
    const initial = setTimeout(() => {
      publish();
      setReady(true);
    }, 0);
    const restore = () => {
      pendingScroll.current = controller.current!.restore();
      publish();
    };
    // A popstate can be followed by hashchange; restoring twice is harmless.
    window.addEventListener('popstate', restore);
    window.addEventListener('hashchange', restore);
    const remember = () => controller.current?.rememberScroll();
    window.addEventListener('pagehide', remember);
    let scrollFrame = 0;
    const onScroll = () => {
      cancelAnimationFrame(scrollFrame);
      scrollFrame = requestAnimationFrame(() => {
        if (pendingScroll.current === null) remember();
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      clearTimeout(initial);
      cancelAnimationFrame(scrollFrame);
      window.history.scrollRestoration = restoration;
      window.removeEventListener('popstate', restore);
      window.removeEventListener('hashchange', restore);
      window.removeEventListener('pagehide', remember);
      window.removeEventListener('scroll', onScroll);
    };
  }, [publish]);

  useEffect(() => {
    if (pendingScroll.current === null) return;
    const y = pendingScroll.current;
    // Wait for React and the sheet's scroll lock to release before restoring.
    const frame = requestAnimationFrame(() => {
      window.scrollTo({ top: y, behavior: 'instant' });
      pendingScroll.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [state]);

  const navigate = useCallback(
    (patch: Partial<NavigationState>, replace = false) => {
      const nav = controller.current;
      if (!nav) return;
      const previousView = nav.state.view;
      if (nav.navigate(patch, replace)) {
        if (nav.state.view !== previousView) pendingScroll.current = 0;
        publish();
      }
    },
    [publish],
  );
  const back = useCallback(() => {
    controller.current?.rememberScroll();
    controller.current?.back();
    publish();
  }, [publish]);
  return { ...state, ready, navigate, back, backLabel };
}
