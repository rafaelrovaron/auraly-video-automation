import { useCallback, useEffect, useRef } from 'react';

export function useUnsavedChanges(dirty: boolean): () => void {
  const unsaved = useRef(dirty);
  unsaved.current = dirty;
  const clear = useCallback(() => { unsaved.current = false; }, []);
  useEffect(() => {
    let acceptedHash = window.location.hash;
    const warning = 'Há um rascunho não salvo. Descartar e sair?';
    const unload = (event: BeforeUnloadEvent) => {
      if (unsaved.current) { event.preventDefault(); event.returnValue = ''; }
    };
    const click = (event: MouseEvent) => {
      if (!unsaved.current || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest('a') : null;
      if (!link || link.target || !link.getAttribute('href')?.startsWith('#') || link.hash === window.location.hash) return;
      if (!window.confirm(warning)) event.preventDefault();
      else { unsaved.current = false; acceptedHash = link.hash; }
    };
    const hash = (event: Event) => {
      if (window.location.hash === acceptedHash) return;
      if (unsaved.current && !window.confirm(warning)) {
        window.history.replaceState(null, '', acceptedHash || '#/campaigns');
        event.stopImmediatePropagation();
      }
      else { unsaved.current = false; acceptedHash = window.location.hash; }
    };
    window.addEventListener('beforeunload', unload);
    document.addEventListener('click', click, true);
    window.addEventListener('hashchange', hash, true);
    window.addEventListener('popstate', hash, true);
    return () => {
      window.removeEventListener('beforeunload', unload);
      document.removeEventListener('click', click, true);
      window.removeEventListener('hashchange', hash, true);
      window.removeEventListener('popstate', hash, true);
    };
  }, []);
  return clear;
}
