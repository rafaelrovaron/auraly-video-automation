import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const root = fileURLToPath(new URL('.', import.meta.url));
const proxy = { target: 'http://127.0.0.1:8000', changeOrigin: false };

export default defineConfig({
  root,
  envDir: root,
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true, cors: false,
    fs: { strict: true, allow: [root] },
    proxy: { '/api': proxy, '/health': proxy },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/testSetup.ts'] },
});
