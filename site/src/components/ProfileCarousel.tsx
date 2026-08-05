import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'

// Fan carousel of real persona screens. Each card is an actual app screenshot
// with a badge label; the deck auto-advances and any side card can be clicked
// to bring it to the front. Card size and fan spread scale with the container
// so it never overflows on narrow screens.
const SCREENS = [
  { badge: 'PROFILES', src: 'screens/profiles.png' },
  { badge: 'NETWORK', src: 'screens/network.png' },
  { badge: 'BOOKMARKS', src: 'screens/bookmarks.png' },
  { badge: 'TAGS', src: 'screens/tags.png' },
  { badge: 'AUTOMATION', src: 'screens/connect.png' },
]

// screenshots are 1300x832 → aspect ~0.64
const ASPECT = 832 / 1300

export default function ProfileCarousel() {
  const [active, setActive] = useState(0)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [cw, setCw] = useState(720)
  const n = SCREENS.length

  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setCw(el.clientWidth))
    ro.observe(el)
    setCw(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const t = setInterval(() => setActive((a) => (a + 1) % n), 3800)
    return () => clearInterval(t)
  }, [n])

  // card width caps at 560 but shrinks to fit small containers, leaving room
  // for the two side cards to peek in.
  const cardW = Math.min(560, Math.max(240, cw * 0.72))
  const offset = cardW * 0.42 // fan spread proportional to card size
  const cardH = cardW * ASPECT + 32 // +chrome bar
  const deckH = cardH + 60

  return (
    <div
      ref={wrapRef}
      className="relative mx-auto flex w-full max-w-4xl items-center justify-center overflow-hidden [perspective:1600px]"
      style={{ height: deckH }}
    >
      {SCREENS.map((screen, i) => {
        let off = i - active
        if (off > n / 2) off -= n
        if (off < -n / 2) off += n
        const isActive = off === 0
        const abs = Math.abs(off)
        // hide far cards on tiny screens so they don't cram
        const hidden = cw < 520 && abs > 1
        return (
          <motion.button
            key={screen.src}
            onClick={() => setActive(i)}
            className="absolute cursor-pointer"
            style={{ width: cardW, transformOrigin: 'bottom center' }}
            animate={{
              x: off * offset,
              rotate: off * 7,
              y: abs * 16,
              scale: isActive ? 1 : 0.82,
              opacity: hidden ? 0 : 1,
              filter: isActive ? 'brightness(1)' : 'brightness(0.5)',
              zIndex: 10 - abs,
            }}
            transition={{ type: 'spring', stiffness: 240, damping: 30 }}
          >
            <div className="relative overflow-hidden rounded-2xl border border-edge2 bg-panel shadow-[0_30px_80px_rgba(0,0,0,0.65)]">
              {/* window chrome */}
              <div className="flex h-8 items-center gap-1.5 border-b border-edge bg-[#161616] px-3">
                <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
                <span className="ml-auto rounded-full border border-edge2 bg-bg2 px-2.5 py-0.5 text-[10px] font-bold tracking-[0.1em] text-sub">
                  {screen.badge}
                </span>
              </div>
              <img src={screen.src} alt={`persona ${screen.badge}`} className="block w-full" draggable={false} />
            </div>
          </motion.button>
        )
      })}

      <div className="absolute bottom-1 left-1/2 z-20 flex -translate-x-1/2 gap-2">
        {SCREENS.map((_, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className="h-1.5 rounded-full bg-lime transition-all"
            style={{ width: i === active ? 24 : 8, opacity: i === active ? 1 : 0.35 }}
            aria-label={`Show screen ${i + 1}`}
          />
        ))}
      </div>
    </div>
  )
}
