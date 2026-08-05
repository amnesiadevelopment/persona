import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// The site is served from the project GitHub Pages path
// (amnesiadevelopment.github.io/persona/) and its build output must land in the
// repo's docs/ folder, which Pages serves from main.
export default defineConfig({
  base: '/persona/',
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  build: {
    outDir: path.resolve(__dirname, '../docs'),
    emptyOutDir: true,
  },
})
