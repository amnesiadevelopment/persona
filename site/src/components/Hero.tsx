import { motion } from 'framer-motion'
import { Download } from 'lucide-react'
import Velaris from './Velaris'
import ProfileCarousel from './ProfileCarousel'
import { GithubIcon } from '@/lib/brand-icons'
import { links } from '@/lib/data'

export default function Hero() {
  return (
    <header className="relative">
      {/* WebGL lime aurora background, fading to black at the bottom */}
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <Velaris height="100%" className="h-full" />
        <div className="absolute inset-0 bg-gradient-to-b from-bg/20 via-bg/50 to-bg" />
        {/* solid fade over the bottom edge so the canvas melts into the page
            with no hard seam at the header boundary */}
        <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-b from-transparent to-bg" />
      </div>

      <div className="wrap pt-24 text-center">
        <motion.span
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="inline-flex items-center gap-2 rounded-full border border-edge2 bg-panel/70 px-3.5 py-1.5 text-[13px] text-sub backdrop-blur"
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lime shadow-[0_0_8px] shadow-lime" />
          Open source · Windows · macOS · Linux
        </motion.span>

        <motion.h1
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, delay: 0.05 }}
          className="mx-auto mt-6 max-w-4xl text-5xl font-extrabold leading-[1.02] tracking-[-0.03em] sm:text-[68px]"
        >
          Run every account
          <br />
          <span className="grad-text animate-shine">as its own person.</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, delay: 0.12 }}
          className="mx-auto mt-5 max-w-xl text-lg text-sub"
        >
          A local, single-operator anti-detect browser manager. Each persona gets its own
          fingerprint, proxy and identity — worked by hand, never linked.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, delay: 0.18 }}
          className="mt-8 flex flex-wrap items-center justify-center gap-3.5"
        >
          <a
            href="#download"
            className="group relative inline-flex items-center gap-2.5 overflow-hidden rounded-xl bg-lime px-7 py-3.5 text-base font-bold text-black shadow-[0_8px_30px] shadow-lime/25 transition-transform hover:-translate-y-0.5"
          >
            <Download className="h-[18px] w-[18px]" />
            Download persona
          </a>
          <a
            href={links.repo}
            className="inline-flex items-center gap-2.5 rounded-xl border border-edge2 bg-panel/70 px-6 py-3.5 text-base backdrop-blur transition-colors hover:border-lime-dim"
          >
            <GithubIcon className="h-[18px] w-[18px]" />
            View source
          </a>
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.24 }}
          className="mt-4 text-[13px] text-dim"
        >
          Free forever · MIT licensed · no telemetry
        </motion.p>

        {/* browser-mockup screenshot */}
        <motion.div
          initial={{ opacity: 0, y: 40, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.7, delay: 0.28, ease: [0.22, 1, 0.36, 1] }}
          className="relative mx-auto mt-10 max-w-3xl pb-24"
        >
          <div className="absolute inset-x-10 top-10 bottom-16 -z-10 rounded-full bg-lime/15 blur-3xl" />
          <ProfileCarousel />
        </motion.div>
      </div>
    </header>
  )
}
