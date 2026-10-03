import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.location.hash = '';
});
