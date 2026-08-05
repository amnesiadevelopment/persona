import { useEffect, useRef, useState } from 'react'
import { useAnimationFrame } from 'framer-motion'
import SpinningGlobe from './SpinningGlobe'

const BAR_COUNT = 40

// Live proxy-throughput card: traffic bars flow like real network activity, the
// ping holds a value for a couple of seconds then jumps to a new one (like a
// real latency read), and an Earth-style globe spins. Bars are driven by an
// animation-frame loop writing to refs; the ping is stepped on a timer.
export default function GeoLiveVisual() {
  const barRefs = useRef<(HTMLSpanElement | null)[]>([])
  const pulseRef = useRef<HTMLSpanElement | null>(null)
  const [ping, setPing] = useState(17)

  // realistic stepped ping: pick a new value, hold it for a random 1.5–5s
  useEffect(() => {
    let timer: number
    const step = () => {
      // bias toward low values, occasional spikes — like a real proxy
      const spike = Math.random() < 0.25
      const next = spike
        ? 20 + Math.round(Math.random() * 15) // 20–35 ms spike
        : 1 + Math.round(Math.random() * 18) //  1–19 ms normal
      setPing(next)
      const hold = 1500 + Math.random() * 3500 // hold 1.5–5s
      timer = window.setTimeout(step, hold)
    }
    timer = window.setTimeout(step, 2000)
    return () => window.clearTimeout(timer)
  }, [])

  useAnimationFrame((t) => {
    const time = t / 1000
    // slower scrolling composite wave
    for (let i = 0; i < BAR_COUNT; i++) {
      const el = barRefs.current[i]
      if (!el) continue
      const wave =
        0.5 +
        0.32 * Math.sin(i * 0.5 - time * 1.4) +
        0.18 * Math.sin(i * 0.17 + time * 0.8)
      const h = Math.max(0.12, Math.min(1, wave))
      el.style.transform = `scaleY(${h.toFixed(3)})`
    }
    // gentle pulse
    if (pulseRef.current) {
      const beat = 0.55 + 0.45 * Math.abs(Math.sin(time * 1.6))
      pulseRef.current.style.opacity = beat.toFixed(2)
      pulseRef.current.style.transform = `scale(${(0.8 + beat * 0.5).toFixed(2)})`
    }
  })

  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[13px]">
        <span className="inline-flex items-center gap-1.5 text-sub">
          <SpinningGlobe size={15} /> Exit IP
        </span>
        <span className="inline-flex items-center gap-1.5 font-mono text-lime">
          <span
            ref={pulseRef}
            className="h-1.5 w-1.5 rounded-full bg-lime shadow-[0_0_6px] shadow-lime"
          />
          matched ·{' '}
          {/* fixed-width, right-aligned so the digit count never shifts layout */}
          <span className="inline-block w-[42px] text-right tabular-nums">{ping} ms</span>
        </span>
      </div>
      <div className="flex h-16 items-end gap-[3px]">
        {Array.from({ length: BAR_COUNT }).map((_, i) => (
          <span
            key={i}
            ref={(el) => {
              barRefs.current[i] = el
            }}
            className="h-full flex-1 rounded-sm bg-gradient-to-t from-lime/30 to-lime"
            style={{ transformOrigin: 'bottom', transform: 'scaleY(0.3)' }}
          />
        ))}
      </div>
    </div>
  )
}
