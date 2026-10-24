import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5300,
    proxy: {
      // Proxy API calls to the FastAPI backend running on port 8500
      '/api': {
        target: 'http://127.0.0.1:8500',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
