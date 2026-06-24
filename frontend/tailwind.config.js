/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // TIKT editorial palette — exact values from the design reference.
        tikt: {
          cream: "#FAF7F2", // app background
          sand: "#F4EFE6", // sidebar
          panel: "#FFFDF9", // cards / inputs
          tan: "#F3EEE4", // avatars / chips
          border: "#E6DECF", // hairline borders
          hover: "#EDE6D9", // hover surface
          ink: "#1C1C1C", // primary text
          body: "#4A4337", // sidebar text
          muted: "#6B6459", // secondary text
          faint: "#A89B7C", // tertiary text / icons
          green: "#1B4332", // brand / buttons / active
          greenDark: "#143728", // button hover
          greenMid: "#23543F", // lighter green
          pos: "#2D6A4F", // positive %
          neg: "#A8432F", // negative %
          gold: "#C9A84C", // accent
          goldHover: "#D8B95E", // accent hover
          dark: "#10231A", // near-black green — top markets ticker
        },
      },
      fontFamily: {
        serif: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"Source Sans 3"', "system-ui", "sans-serif"],
        // Company page typography (fonts loaded in index.html).
        display: ['"Playfair Display"', "Georgia", "serif"],
        inter: ['"Inter"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
