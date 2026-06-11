/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // RAG System design tokens
        surface: {
          0: "#0E0F14",   // deepest bg
          1: "#14151C",   // main bg
          2: "#1C1E28",   // card / panel bg
          3: "#242736",   // elevated card
          4: "#2E3247",   // hover states
        },
        accent: {
          DEFAULT: "#6C7BFF",  // electric indigo — primary action
          dim:     "#3D4ACC",
          glow:    "rgba(108,123,255,0.15)",
        },
        teal: {
          DEFAULT: "#2DD4C4",  // citations & retrieval indicators
          dim:     "#1A8F84",
        },
        amber: {
          DEFAULT: "#F5A623",  // warnings / confidence scores
        },
        red: {
          DEFAULT: "#FF5C6A",  // errors
        },
        text: {
          primary:   "#F0F2FF",
          secondary: "#8B90B0",
          muted:     "#555A78",
        },
      },
      fontFamily: {
        sans:  ["Inter", "system-ui", "sans-serif"],
        mono:  ["JetBrains Mono", "Fira Code", "monospace"],
        display: ["'DM Sans'", "Inter", "sans-serif"],
      },
      borderRadius: {
        sm: "6px",
        DEFAULT: "10px",
        md: "12px",
        lg: "16px",
        xl: "20px",
      },
      boxShadow: {
        glow:    "0 0 20px rgba(108,123,255,0.25)",
        "glow-sm": "0 0 10px rgba(108,123,255,0.15)",
        card:    "0 4px 24px rgba(0,0,0,0.4)",
      },
      animation: {
        "fade-in":    "fadeIn 0.2s ease-out",
        "slide-up":   "slideUp 0.25s ease-out",
        "pulse-dot":  "pulseDot 1.4s ease-in-out infinite",
        "stream":     "stream 0.1s ease-out",
      },
      keyframes: {
        fadeIn:    { from: { opacity: 0 }, to: { opacity: 1 } },
        slideUp:   { from: { opacity: 0, transform: "translateY(8px)" }, to: { opacity: 1, transform: "translateY(0)" } },
        pulseDot:  { "0%, 80%, 100%": { opacity: 0.2, transform: "scale(0.8)" }, "40%": { opacity: 1, transform: "scale(1)" } },
        stream:    { from: { opacity: 0 }, to: { opacity: 1 } },
      },
    },
  },
  plugins: [],
};
