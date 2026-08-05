import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChromiumPrimary, FirefoxPrimary } from './EngineMockups'

type EngineCopy = {
  tag: string
  tagColor: string
  glow: string
  title: string
  body: string
  Mockup: () => React.ReactNode
}

const ENGINES: EngineCopy[] = [
  {
    tag: 'CHROMIUM',
    tagColor: '#7ea8ff',
    glow: 'rgba(63,107,255,0.20)',
    title: 'fingerprint-chromium',
    body: 'An ungoogled build with canvas, WebGL, audio, fonts, hardware and platform spoofed deterministically. Extensions add locale and per-profile entropy — available on every OS.',
    Mockup: ChromiumPrimary,
  },
  {
    tag: 'FIREFOX',
    tagColor: '#ffab63',
    glow: 'rgba(255,138,61,0.20)',
    title: 'patched Firefox 150+',
    body: 'Spoofing done at the C++ level — no CDP or webdriver tells. The most invisible profile when that’s what matters, on every desktop OS.',
    Mockup: FirefoxPrimary,
  },
]

export default function EngineSwap() {
  const ref = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(0)

  // Drive the active engine from how far the tall container has scrolled
  // through the viewport. Robust and simple — no useScroll quirks.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => {
      const rect = el.getBoundingClientRect()
      const total = rect.height - window.innerHeight
      if (total <= 0) return
      const progress = Math.min(1, Math.max(0, -rect.top / total))
      setActive(progress < 0.5 ? 0 : 1)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  const e = ENGINES[active]

  return (
    <div ref={ref} className="relative h-[200vh]">
      <div className="sticky top-0 flex h-[86vh] items-center overflow-hidden">
        {/* ambient engine glow */}
        <motion.div
          key={`glow-${active}`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6 }}
          className="pointer-events-none absolute left-1/2 top-1/2 h-[520px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full blur-[120px]"
          style={{ background: e.glow }}
        />

        <div className="wrap w-full">
          <div className="grid grid-cols-1 items-center gap-10 md:grid-cols-2">
            {/* text */}
            <div className="relative min-h-[220px]">
              <AnimatePresence mode="wait">
                <motion.div
                  key={`text-${active}`}
                  initial={{ opacity: 0, y: 24 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -24 }}
                  transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                  className="flex flex-col items-start gap-4"
                >
                  <div className="text-xs font-bold tracking-wide" style={{ color: e.tagColor }}>
                    {e.tag}
                  </div>
                  <h3 className="text-3xl font-extrabold tracking-[-0.03em] md:text-[40px]">
                    {e.title}
                  </h3>
                  <p className="max-w-[520px] text-[15px] leading-6 text-sub">{e.body}</p>
                </motion.div>
              </AnimatePresence>
            </div>

            {/* mockup */}
            <div className="relative mx-auto h-[340px] w-full max-w-[440px]">
              <AnimatePresence mode="wait">
                <motion.div
                  key={`mock-${active}`}
                  initial={{ opacity: 0, y: 24, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -24, scale: 0.97 }}
                  transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                  className="absolute inset-0"
                >
                  <div
                    className="absolute inset-0 -z-10 translate-x-5 translate-y-5 rounded-[28px] opacity-60 blur-[2px]"
                    style={{ background: `linear-gradient(160deg, ${e.tagColor}22, #090909 60%)` }}
                  />
                  <div className="h-full overflow-hidden rounded-[28px] border border-edge2 bg-white/[0.04] backdrop-blur-[15px]">
                    <e.Mockup />
                  </div>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </div>

        {/* which-engine dots */}
        <div className="pointer-events-none absolute bottom-8 left-1/2 flex -translate-x-1/2 gap-2">
          {ENGINES.map((_, i) => (
            <span
              key={i}
              className="h-1.5 w-6 rounded-full bg-lime transition-opacity duration-300"
              style={{ opacity: active === i ? 1 : 0.3 }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
