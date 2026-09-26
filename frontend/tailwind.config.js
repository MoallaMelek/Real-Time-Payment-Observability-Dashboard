/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#080b10",
        panel: "#101720",
        line: "#223044",
        mint: "#35d49a",
        danger: "#ff5e6c",
        amber: "#f3ba4d",
        cyan: "#43c6f4",
      },
      boxShadow: {
        glow: "0 0 28px rgba(53, 212, 154, 0.16)",
      },
    },
  },
  plugins: [],
};
