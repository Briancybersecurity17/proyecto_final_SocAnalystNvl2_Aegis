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
      },
    },
  },
  plugins: [],
}
