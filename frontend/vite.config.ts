import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 同源多页站点：/app/（首页）、/app/user/（用户调用平台）、/app/admin/（管理平台）
// 由 scheduler 的 StaticFiles 挂载目录服务，因此各入口使用相对资源路径基础 base '/app/'。
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        user: resolve(__dirname, 'user/index.html'),
        admin: resolve(__dirname, 'admin/index.html'),
      },
    },
  },
});
