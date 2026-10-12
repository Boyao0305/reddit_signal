/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./seedcast.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        seed: {
          band: '#3A1670',
          bg: '#F5F2FA',
          ink: '#1D1433',
          ink2: '#4A3F66',
          muted: '#6A5F82',
          line: '#E6DFF1',
          accent: '#632CA6',
          tint: '#EFE8FA',
          b2b: '#3A1A6B',
        },
      },
      fontFamily: {
        seed: ['"DM Sans"', 'system-ui', 'sans-serif'],
        seedmono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
