import { useId } from 'react'

// An infinity (figure-eight) mark that shimmers: a bright band slides smoothly
// across the whole loop via an animated gradient, so the colour flows/glints
// along the ∞ instead of a hard segment chasing round it.
const PATH =
  'M30 32c0-9 6-15 14-15s10 6 16 15 9 15 16 15 14-6 14-15-6-15-14-15-10 6-16 15-9 15-16 15-14-6-14-15z'

export default function InfinityFlow({ width = 150 }: { width?: number }) {
  const id = useId().replace(/:/g, '')
  const gradId = `inf-grad-${id}`
  return (
    <svg viewBox="0 0 120 64" width={width} height={(width * 64) / 120} fill="none" aria-hidden>
      <defs>
        {/* a wide gradient whose bright highlight sweeps left↔right; the
            gradientTransform animation slides it across the mark for a liquid
            shimmer rather than a running dash */}
        <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#5f7d00" />
          <stop offset="0.35" stopColor="#97ca00" />
          <stop offset="0.5" stopColor="#eaffb0" />
          <stop offset="0.65" stopColor="#97ca00" />
          <stop offset="1" stopColor="#5f7d00" />
          <animateTransform
            attributeName="gradientTransform"
            type="translate"
            values="-0.6 0; 0.6 0; -0.6 0"
            dur="3.6s"
            repeatCount="indefinite"
            calcMode="spline"
            keyTimes="0; 0.5; 1"
            keySplines="0.45 0 0.55 1; 0.45 0 0.55 1"
          />
        </linearGradient>
      </defs>

      {/* dim base loop */}
      <path d={PATH} stroke="#3f5a00" strokeWidth="11" strokeLinecap="round" />

      {/* shimmering coloured loop */}
      <path
        d={PATH}
        stroke={`url(#${gradId})`}
        strokeWidth="11"
        strokeLinecap="round"
        style={{ filter: 'drop-shadow(0 0 3px #97ca0066)' }}
      />
    </svg>
  )
}
