import { useId } from 'react'

// A globe that rotates like Earth: latitude lines are fixed, and the longitude
// meridians sweep across the face (right → left, i.e. west→east rotation) with
// a perspective squash near the edges, so it reads as a turning sphere rather
// than a flat spinning wheel. Pure SMIL so it runs on the GPU with no JS.
const DUR = 5 // seconds per rotation

export default function SpinningGlobe({ size = 16 }: { size?: number }) {
  const id = useId().replace(/:/g, '')
  const clip = `globe-clip-${id}`
  const meridians = [0, 1, 2]

  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" aria-hidden>
      <defs>
        <clipPath id={clip}>
          <circle cx="12" cy="12" r="9" />
        </clipPath>
      </defs>

      {/* sphere outline */}
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.4" />

      <g clipPath={`url(#${clip})`} stroke="currentColor" fill="none">
        {/* latitude lines — fixed, curved toward the poles */}
        <g strokeWidth="1" opacity="0.7">
          <path d="M3.6 8.2 Q12 6.4 20.4 8.2" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <path d="M3.6 15.8 Q12 17.6 20.4 15.8" />
        </g>

        {/* meridians: ellipses whose horizontal radius breathes (perspective)
            and centre sweeps right→left across the face. Three phase-shifted
            copies give a continuous west→east rotation. */}
        <g strokeWidth="1" opacity="0.85">
          {meridians.map((k) => {
            const begin = `${-(DUR * k) / meridians.length}s`
            return (
              <ellipse key={k} cx="12" cy="12" ry="9">
                {/* rx: thin at the right edge → widest at centre → thin at the
                    left edge, then the meridian resets to the right. */}
                <animate
                  attributeName="rx"
                  dur={`${DUR}s`}
                  begin={begin}
                  repeatCount="indefinite"
                  values="0;7.5;0;0"
                  keyTimes="0;0.5;1;1"
                  calcMode="spline"
                  keySplines="0.4 0 0.6 1;0.4 0 0.6 1;0 0 1 1"
                />
                {/* cx: travels continuously right → left (west→east), then jumps
                    back to the right edge while rx is 0 (invisible). */}
                <animate
                  attributeName="cx"
                  dur={`${DUR}s`}
                  begin={begin}
                  repeatCount="indefinite"
                  values="19;5;19"
                  keyTimes="0;1;1"
                />
              </ellipse>
            )
          })}
        </g>
      </g>
    </svg>
  )
}
