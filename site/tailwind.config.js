/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#050505',
        bg2: '#0b0b0b',
        panel: '#111111',
        panel2: '#161616',
        edge: '#232323',
        edge2: '#2e2e2e',
        ink: '#f4f4f5',
        sub: '#9a9a9a',
        dim: '#6a6a6a',
        lime: {
          DEFAULT: '#97ca00',
          bright: '#b6f000',
          dim: '#4f6a00',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      maxWidth: {
        wrap: '1100px',
      },
      keyframes: {
        marquee: {
          from: { transform: 'translateX(0)' },
          to: { transform: 'translateX(-50%)' },
        },
        shine: {
          to: { backgroundPosition: '200% center' },
        },
      },
      animation: {
        marquee: 'marquee 32s linear infinite',
        shine: 'shine 5s linear infinite',
      },
    },
  },
  plugins: [],
}

