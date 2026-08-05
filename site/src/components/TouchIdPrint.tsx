import { motion } from 'framer-motion'
import { useId } from 'react'

// A Touch-ID-style fingerprint: real ridge lines (canonical lucide fingerprint)
// that fill with the lime accent from the bottom up, on a loop — like
// registering a print on iOS. A rising gradient mask reveals a coloured copy
// over a dim base.

const RIDGES = [
  'M12 10a2 2 0 0 0-2 2c0 1.02-.1 2.51-.26 4',
  'M14 13.12c0 2.38 0 6.38-1 8.88',
  'M17.29 21.02c.12-.6.43-2.3.5-3.02',
  'M2 12a10 10 0 0 1 18-6',
  'M2 16h.01',
  'M21.8 16c.2-2 .131-5.354 0-6',
  'M5 19.5C5.5 18 6 15 6 12a6 6 0 0 1 .34-2',
  'M8.65 22c.21-.66.45-1.32.57-2',
  'M9 6.8a6 6 0 0 1 9 5.2v2',
]

export default function TouchIdPrint({ size = 132 }: { size?: number }) {
  const id = useId().replace(/:/g, '')
  const maskId = `tid-mask-${id}`
  const gradId = `tid-grad-${id}`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className="overflow-visible"
      aria-hidden
    >
      <defs>
        {/* very soft radial feather so the growing edge never reads as a hard
            line crossing the print */}
        <radialGradient id={gradId}>
          <stop offset="0%" stopColor="#fff" />
          <stop offset="45%" stopColor="#fff" />
          <stop offset="100%" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
        <mask id={maskId} maskUnits="userSpaceOnUse" x="-8" y="-8" width="40" height="40">
          {/* a circle that grows from the print's centre outward, then resets —
              Touch-ID style inside-out fill. Radius overshoots the print so at
              the hold the whole print sits inside the solid core (no ring). */}
          <motion.circle
            cx="12"
            cy="13"
            fill={`url(#${gradId})`}
            initial={{ r: 0 }}
            animate={{ r: [0, 34, 34, 0] }}
            transition={{
              duration: 3,
              times: [0, 0.5, 0.72, 1],
              repeat: Infinity,
              ease: 'easeInOut',
            }}
          />
        </mask>
      </defs>

      {/* dim base ridges (always visible) */}
      <g stroke="#3a3a3a" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="none">
        {RIDGES.map((d, i) => (
          <path key={i} d={d} />
        ))}
      </g>

      {/* lime-lit ridges, revealed by the rising mask */}
      <g
        stroke="#97ca00"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
        mask={`url(#${maskId})`}
        style={{ filter: 'drop-shadow(0 0 3px #97ca0099)' }}
      >
        {RIDGES.map((d, i) => (
          <path key={i} d={d} />
        ))}
      </g>
    </svg>
  )
}
