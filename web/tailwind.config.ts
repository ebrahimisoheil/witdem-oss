import { wedgesTW } from "@lemonsqueezy/wedges";
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}", "./node_modules/@lemonsqueezy/wedges/dist/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: { extend: {} },
  plugins: [wedgesTW({ defaultTheme: "light", defaultExtendTheme: "light" })],
} satisfies Config;
