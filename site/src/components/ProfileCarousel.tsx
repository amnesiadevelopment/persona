import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'

// Fan carousel of real persona screens. Each card is an actual app screenshot
// with a badge label; the deck auto-advances and any side card can be clicked
// to bring it to the front. Desktop keeps the original spacious fan; below
// 600px it shrinks to a single card so it fits a phone.
const SCREENS = [
  { badge: 'PROFILES', src: 'screens/profiles.png' },
  { badge: 'NETWORK', src: 'screens/network.png' },
  { badge: 'BOOKMARKS', src: 'screens/bookmarks.png' },
  { badge: 'TAGS', src: 'screens/tags.png' },
  { badge: 'AUTOMATION', src: 'screens/connect.png' },
]

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

  const mobile = cw < 600
  const cardW = mobile ? Math.max(240, cw * 0.86) : 560
  const offset = mobile ? 0 : 210
  const deckH = mobile ? cardW * ASPECT + 70 : 440

  return (
    <div
      ref={wrapRef}
      className="relative mx-auto flex w-full max-w-4xl items-center justify-center [perspective:1600px]"
      style={{ height: deckH, overflow: mobile ? 'hidden' : 'visible' }}
    >
      {SCREENS.map((screen, i) => {
        let off = i - active
        if (off > n / 2) off -= n
        if (off < -n / 2) off += n
        const isActive = off === 0
        const abs = Math.abs(off)
        // phones show only the active card
        const hidden = mobile && abs > 0
        return (
          <motion.button
            key={screen.src}
            onClick={() => setActive(i)}
            className="absolute cursor-pointer"
            style={{ width: cardW, transformOrigin: 'bottom center' }}
            animate={{
              x: off * offset,
              rotate: mobile ? 0 : off * 7,
              y: mobile ? 0 : abs * 18,
              scale: isActive ? 1 : 0.82,
              opacity: hidden ? 0 : 1,
              filter: isActive ? 'brightness(1)' : 'brightness(0.5)',
              zIndex: 10 - abs,
            }}
            transition={{ type: 'spring', stiffness: 240, damping: 30 }}
          >
            {/* Desktop keeps the original shadow. On mobile the single card
                sits on the lime hero, where a hard drop shadow reads as a ring
                around the card — so no shadow there. */}
            <div
              className={
                'relative overflow-hidden rounded-2xl border border-edge2 bg-panel ' +
                (mobile ? '' : 'shadow-[0_30px_80px_rgba(0,0,0,0.65)]')
              }
            >
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

      <div className="absolute bottom-1 left-1/2 z-20 flex -translate-x-1/2 gap-2 md:-bottom-2">
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
