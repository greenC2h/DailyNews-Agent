import { defineConfig } from 'vite'
export default defineConfig({ resolve: { alias: { vue: 'vue/dist/vue.esm-bundler.js' } }, server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } }, build: { outDir: 'dist' } })
