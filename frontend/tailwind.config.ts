import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Core neutral palette (zinc-based)
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",

        // Causal confidence tokens
        confidence: {
          high: "hsl(var(--confidence-high))",
          "high-fg": "hsl(var(--confidence-high-fg))",
          medium: "hsl(var(--confidence-medium))",
          "medium-fg": "hsl(var(--confidence-medium-fg))",
          low: "hsl(var(--confidence-low))",
          "low-fg": "hsl(var(--confidence-low-fg))",
        },

        // Cynefin domain tokens
        cynefin: {
          clear: "hsl(var(--cynefin-clear))",
          "clear-fg": "hsl(var(--cynefin-clear-fg))",
          complicated: "hsl(var(--cynefin-complicated))",
          "complicated-fg": "hsl(var(--cynefin-complicated-fg))",
          complex: "hsl(var(--cynefin-complex))",
          "complex-fg": "hsl(var(--cynefin-complex-fg))",
          chaotic: "hsl(var(--cynefin-chaotic))",
          "chaotic-fg": "hsl(var(--cynefin-chaotic-fg))",
          disorder: "hsl(var(--cynefin-disorder))",
          "disorder-fg": "hsl(var(--cynefin-disorder-fg))",
        },

        // Guardian verdict tokens
        verdict: {
          approved: "hsl(var(--verdict-approved))",
          "approved-fg": "hsl(var(--verdict-approved-fg))",
          rejected: "hsl(var(--verdict-rejected))",
          "rejected-fg": "hsl(var(--verdict-rejected-fg))",
          escalation: "hsl(var(--verdict-escalation))",
          "escalation-fg": "hsl(var(--verdict-escalation-fg))",
          pending: "hsl(var(--verdict-pending))",
          "pending-fg": "hsl(var(--verdict-pending-fg))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      keyframes: {
        "thinking-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
        "thinking-spin": {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },
        "slide-in-from-top": {
          from: { transform: "translateY(-100%)" },
          to: { transform: "translateY(0)" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
      },
      animation: {
        "thinking-pulse": "thinking-pulse 1.5s ease-in-out infinite",
        "thinking-spin": "thinking-spin 1s linear infinite",
        "slide-in": "slide-in-from-top 0.2s ease-out",
        "fade-in": "fade-in 0.15s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
