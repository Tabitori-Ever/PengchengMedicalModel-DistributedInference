import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 测试与性能对比站点（benchmark-site v1.0）—— 独立部署，不依赖 scheduler 存活。
// 生产：站点后端（FastAPI，§6.1）同源挂载本 dist/，因此 base 为 '/'，API 走 /api。
// 开发：vite dev server 把 /api 代理到本机站点后端 8099，无需 CORS。
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    host: '0.0.0.0',
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8099', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    emptyOutDir: true,
  },
});
