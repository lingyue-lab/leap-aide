import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

export default defineConfig({
  plugins: [react()],
  // noVNC 1.7.0 uses top-level await; must target ES2022+ in both
  // build (Rollup) and dev (esbuild) modes.
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
      // @novnc/novnc has no "exports" map; alias the deep import directly
      // to the physical file so Vite bypasses exports-map resolution.
      '@novnc/novnc/core/rfb.js': path.resolve(
        __dirname,
        'node_modules/@novnc/novnc/core/rfb.js'
      ),
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    // noVNC 1.7.0 uses top-level await; requires ES2022+ target.
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
    // Enable HTTPS for Vite dev server so noVNC 1.x (which requires a secure
    // context) works in dev mode.  Set HERMES_WEBUI_TLS_CERT / TLS_KEY env vars
    // to the same self-signed cert used by the Python backend.
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
        secure: false,   // allow self-signed cert on backend
        cookieDomainRewrite: {
          'localhost': '',
          '*': '',
        },
        // Strip Secure flag from cookies so the browser accepts them
        // even when Vite dev server and backend are on different ports.
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
