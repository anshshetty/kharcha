'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { SourceEmail } from '@/lib/types';

export function useSourceEmail(id: string, onError: (message: string) => void) {
  const [result, setResult] = useState<{
    id: string;
    source: SourceEmail | null;
    sourceError: string;
  }>({ id: '', source: null, sourceError: '' });
  useEffect(() => {
    let cancelled = false;
    if (id) {
      api<SourceEmail>('/sources/' + encodeURIComponent(id))
        .then((value) => {
          if (!cancelled) setResult({ id, source: value, sourceError: '' });
        })
        .catch((error) => {
          if (!cancelled) {
            setResult({ id, source: null, sourceError: error.message });
            onError(error.message);
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [id, onError]);
  return result.id === id ? result : { source: null, sourceError: '' };
}
