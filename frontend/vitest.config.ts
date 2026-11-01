import {defineConfig} from 'vitest/config'
import react from '@vitejs/plugin-react'

// Keep Playwright's e2e/*.spec.ts separate from Vitest's unit tests.
export default defineConfig({
  plugins: [react()],
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
  },
})
