/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        primary: "#1e40af", // indigo‑900 – you can adjust to your brand
        secondary: "#64748b", // slate‑500
        // Tema HUD oscuro (paneles, líneas y texto)
        void: "#050212",
        panel: "#0d0620",
        inset: "#0a0518",
        hud: {
          line: "#1fc8ff",
          dim: "#0b7fb5",
          soft: "#12456a",
          fg: "#bfefff",
          muted: "#3f6b88",
        },
        // Crítica fija por política del proyecto
        sev: {
          critical: "#dc2626",
          high: "#f97316",
          medium: "#facc15",
          low: "#10b981",
        },
      },
      fontFamily: {
        display: ["Orbitron", "sans-serif"],
        mono: ["JetBrains Mono", "Courier New", "monospace"],
      },
    },
  },
  plugins: [],
}
