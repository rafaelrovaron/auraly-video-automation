import { collection, object, read } from './api';
import type { Items } from './api';

export type LocalAsset = {kind: 'font' | 'music' | 'timing'; name: string; path: string; sha256: string};

export function listLocalAssets(signal: AbortSignal): Promise<Items<LocalAsset>> {
  return read('/api/v1/editing/assets', signal, value => collection(value, item => object(item)
    && ['font', 'music', 'timing'].includes(String(item.kind))
    && typeof item.name === 'string' && typeof item.path === 'string'
    && typeof item.sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.sha256)));
}
