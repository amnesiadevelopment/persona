import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'

// Fan carousel of real persona screens. Each card is an actual app screenshot
// with a badge label; the deck auto-advances and any side card can be clicked
// to bring it to the front.
const SCREENS = [
  { badge: 'PROFILES', src: 'screens/profiles.png' },
  { badge: 'NETWORK', src: 'screens/network.png' },
  { badge: 'BOOKMARKS', src: 'screens/bookmarks.png' },
  { badge: 'TAGS', src: 'screens/tags.png' },
  { badge: 'AUTOMATION', src: 'screens/connect.png' },
]

export default function ProfileCarousel() {
  const [active, setActive] = useState(0)
  const n = SCREENS.length

  useEffect(() => {
    const t = setInterval(() => setActive((a) => (a + 1) % n), 3800)
    return () => clearInterval(t)
  }, [n])

  return (
    <div className="relative mx-auto flex h-[440px] w-full max-w-4xl items-center justify-center [perspective:1600px]">
      {SCREENS.map((screen, i) => {
        let off = i - active
        if (off > n / 2) off -= n
        if (off < -n / 2) off += n
        const isActive = off === 0
        const abs = Math.abs(off)
        return (
          <motion.button
            key={screen.src}
            onClick={() => setActive(i)}
            className="absolute cursor-pointer"
            style={{ width: 560, transformOrigin: 'bottom center' }}
            animate={{
              x: off * 210,
              rotate: off * 7,
              y: abs * 18,
              scale: isActive ? 1 : 0.82,
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

      <div className="absolute -bottom-2 left-1/2 z-20 flex -translate-x-1/2 gap-2">
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
