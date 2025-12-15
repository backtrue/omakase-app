/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,jsx,ts,tsx}",
    "./components/**/*.{js,jsx,ts,tsx}",
  ],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      colors: {
        // Omakase brand colors from mockups
        primary: "#1a1a1a",
        secondary: "#666666",
        accent: "#f5f5f5",
        background: "#ffffff",
        surface: "#fafafa",
      },
      fontFamily: {
        sans: ["System"],
        serif: ["Georgia"],
      },
    },
  },
  plugins: [],
};
