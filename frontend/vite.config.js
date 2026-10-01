import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vitest sets VITEST=true before loading this file. Pin the API origin so a
// developer/Docker shell VITE_API_BASE_URL (e.g. http://localhost:8001) cannot
// leak into unit tests. `vite build` / `vite dev` do not set VITEST, so
// production and local runtime configuration are unchanged.
if (process.env.VITEST) {
  process.env.VITE_API_BASE_URL = 'http://localhost:8000'
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    env: {
      VITE_API_BASE_URL: 'http://localhost:8000',
    },
    globals: true,
    clearMocks: true,
    restoreMocks: true,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    coverage: {
      provider: 'v8',
      reportsDirectory: 'coverage',
      reporter: ['text', 'html', 'json-summary'],
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/main.jsx', 'src/test/**'],
      thresholds: { lines: 80 },
    },
  },
})
