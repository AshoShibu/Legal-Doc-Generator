/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        premium: {
          bg: "#f6f1e8",
          surface: "#fffdfa",
          text: "#2d1f19",
          muted: "#6d5a52",
          primary: "#cb7147",
          primaryDark: "#b95f38",
          line: "#e7d8c7",
          danger: "#a5352a",
          ok: "#2f855a",
          accent: "#e8d9fd",
        },
      },
      fontFamily: {
        sans: ["Manrope", "sans-serif"],
        display: ["Fraunces", "serif"],
      },
      boxShadow: {
        premium: "0 20px 35px rgba(56, 38, 30, 0.08)",
      },
      backgroundImage: {
        "premium-page":
          "radial-gradient(circle at 95% 8%, #e8d9fd 0%, rgba(232,217,253,0) 32%), linear-gradient(180deg, #fbf8f2, #f6f1e8)",
      },
    },
  },
  plugins: [],
};
