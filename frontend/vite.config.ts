import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

export default defineConfig({
  plugins: [react()],
  esbuild: {
    target: 'es2022',
  },
  optimizeDeps: {
    esbuildOptions: {
      target: 'es2022',
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    target: 'es2022',
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          i18n: ['i18next', 'react-i18next'],
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 8788,
    https: (() => {
      const cert = process.env.HERMES_WEBUI_TLS_CERT;
      const key  = process.env.HERMES_WEBUI_TLS_KEY;
      if (cert && key) {
        return { cert: fs.readFileSync(cert), key: fs.readFileSync(key) };
      }
      return undefined;
    })(),
    proxy: {
      '/api': {
        target: 'https://localhost:8787',
        changeOrigin: true,
        ws: true,
        secure: false,
        cookieDomainRewrite: {
          'localhost': '',
          '*': '',
        },
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            const cookies = proxyRes.headers['set-cookie'];
            if (cookies) {
              proxyRes.headers['set-cookie'] = cookies.map((c: string) =>
                c
                  .replace(/;\s*Secure/gi, '')
                  .replace(/;\s*SameSite=None/gi, '; SameSite=Lax')
              );
            }
          });
        },
        headers: {
          'X-Forwarded-Host': 'localhost:8787',
        }
      }
    }
  }
});
