import { motion } from 'framer-motion'
import { ArrowRight, Bug, MessageSquare } from 'lucide-react'
import { rise, stagger, viewportOnce } from '@/lib/motion'
import FingerprintScan from './FingerprintScan'
import EngineSwap from './EngineSwap'
import { downloads, links, marqueeItems } from '@/lib/data'
import { AppleIcon, GithubIcon, LinuxIcon, WindowsIcon } from '@/lib/brand-icons'

function Eyebrow({ children }: { children: string }) {
  return (
    <div className="mb-3 inline-flex items-center gap-2 text-[13px] font-bold uppercase tracking-[0.15em] text-lime">
      <span className="h-0.5 w-5 rounded bg-lime" />
      {children}
    </div>
  )
}

/* ---------------- Trust marquee ---------------- */
export function Marquee() {
  const row = [...marqueeItems, ...marqueeItems]
  return (
    <div className="marquee-mask overflow-hidden border-y border-edge bg-bg2 py-5">
      <div className="flex w-max animate-marquee">
        {[0, 1].map((g) => (
          <div key={g} className="flex shrink-0 gap-12 pr-12">
            {row.map((item, i) => (
              <span key={`${g}-${i}`} className="flex items-center gap-2.5 whitespace-nowrap text-sm text-dim">
                <span className="h-1.5 w-1.5 rounded-full bg-lime-dim" />
                {item}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- Engines ---------------- */
export function Engines() {
  return (
    <section id="engines" className="pt-[88px]">
      <div className="wrap">
        <motion.div initial="hidden" whileInView="show" viewport={viewportOnce} variants={rise}>
          <Eyebrow>Two engines</Eyebrow>
          <h2 className="text-4xl font-extrabold tracking-[-0.03em]">
            One manager, two ways to disappear
          </h2>
          <p className="mt-3.5 max-w-2xl text-[17px] text-sub">
            Pick the browser engine per profile. Each persona's fingerprint is seeded
            deterministically from its name — identical across restarts, unrelated between personas.
          </p>
        </motion.div>
      </div>

      <EngineSwap />
    </section>
  )
}

/* ---------------- Download ---------------- */
const osIcon = { Windows: WindowsIcon, macOS: AppleIcon, Linux: LinuxIcon } as const

export function DownloadSection() {
  return (
    <section id="download" className="border-y border-edge bg-bg2 py-[88px]">
      <div className="wrap">
        <motion.div initial="hidden" whileInView="show" viewport={viewportOnce} variants={rise}>
          <Eyebrow>Get it</Eyebrow>
          <h2 className="text-4xl font-extrabold tracking-[-0.03em]">Download for your OS</h2>
          <p className="mt-3.5 max-w-2xl text-[17px] text-sub">
            Grab the latest build. The browser engine downloads itself on first launch.
          </p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={viewportOnce}
          variants={stagger}
          className="mt-8 grid gap-4 sm:grid-cols-3"
        >
          {downloads.map(({ os, file, href, hint }) => {
            const Icon = osIcon[os]
            return (
              <motion.a
                key={os}
                variants={rise}
                href={href}
                className="group flex flex-col items-center gap-2 rounded-2xl border border-edge bg-panel px-6 pb-6 pt-8 text-center transition-all hover:-translate-y-1 hover:border-lime hover:bg-panel2"
              >
                <Icon className="mb-1 h-10 w-10 text-sub transition-all group-hover:-translate-y-0.5 group-hover:scale-105 group-hover:text-lime" />
                <div className="text-[17px] font-bold">{os}</div>
                <div className="text-[12.5px] text-dim">{file}</div>
                <span className="mt-2 rounded-full border border-lime/20 px-2.5 py-1 text-[11.5px] font-semibold tracking-wide text-lime-dim transition-colors group-hover:border-lime group-hover:bg-lime group-hover:text-black">
                  Direct download · {hint}
                </span>
              </motion.a>
            )
          })}
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={viewportOnce}
          variants={rise}
          className="mt-9 rounded-xl border border-edge border-l-[3px] border-l-lime-dim bg-gradient-to-b from-panel to-bg2 px-6 py-5 text-[14.5px] text-sub"
        >
          <b className="text-ink">Honest about limits:</b> persona aims for a clean, consistent,
          human-like profile — not magic invisibility. Passing one fingerprint checker isn't the
          same as passing systems that score TLS + IP + headers + behaviour together. Proxy quality
          and human behaviour matter as much as the fingerprint.
        </motion.div>
      </div>
    </section>
  )
}

/* ---------------- Support ---------------- */
export function Support() {
  const cards = [
    {
      Icon: Bug,
      href: links.newIssue,
      title: 'Report a bug',
      body: 'Open a new issue with what happened, your OS and the engine you used.',
    },
    {
      Icon: MessageSquare,
      href: links.issues,
      title: 'Feature & feedback',
      body: 'Browse open issues or suggest an idea in the tracker.',
    },
  ]
  return (
    <section id="support" className="py-[88px]">
      <div className="wrap">
        <motion.div initial="hidden" whileInView="show" viewport={viewportOnce} variants={rise}>
          <Eyebrow>Support</Eyebrow>
          <h2 className="text-4xl font-extrabold tracking-[-0.03em]">Found a bug? Tell us.</h2>
          <p className="mt-3.5 max-w-2xl text-[17px] text-sub">
            persona is actively developed in the open. Report a bug, request a feature, or just
            leave feedback — everything is tracked on GitHub.
          </p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={viewportOnce}
          variants={stagger}
          className="mt-8 grid gap-[18px] sm:grid-cols-2"
        >
          {cards.map(({ Icon, href, title, body }) => (
            <motion.a
              key={title}
              variants={rise}
              href={href}
              className="group flex items-center gap-[18px] rounded-2xl border border-edge bg-panel px-[26px] py-6 transition-all hover:-translate-y-1 hover:border-lime hover:bg-panel2"
            >
              <div className="grid h-[46px] w-[46px] flex-none place-items-center rounded-xl border border-lime/20 bg-lime/10">
                <Icon className="h-[23px] w-[23px] text-lime" strokeWidth={1.7} />
              </div>
              <div className="flex-1">
                <h3 className="text-[17px] font-semibold">{title}</h3>
                <p className="text-sm text-sub">{body}</p>
              </div>
              <ArrowRight className="h-[22px] w-[22px] flex-none text-dim transition-all group-hover:translate-x-1 group-hover:text-lime" />
            </motion.a>
          ))}
        </motion.div>
      </div>
    </section>
  )
}

/* ---------------- Scanner band ---------------- */
export function Scanner() {
  return (
    <section className="relative overflow-hidden py-24">
      <div className="absolute left-1/2 top-1/2 -z-10 h-[360px] w-[360px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-lime/10 blur-3xl" />
      <motion.div
        initial={{ opacity: 0, scale: 0.92 }}
        whileInView={{ opacity: 1, scale: 1 }}
        viewport={viewportOnce}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col items-center gap-6 text-center"
      >
        <FingerprintScan />
        <div>
          <h2 className="text-3xl font-extrabold tracking-[-0.03em] md:text-4xl">
            Every persona, a <span className="grad-text">different print.</span>
          </h2>
          <p className="mx-auto mt-3 max-w-md text-[15px] text-sub">
            Seeded from its name — identical across restarts, unrelated between personas. No shared
            tells, no cross-linking.
          </p>
        </div>
      </motion.div>
    </section>
  )
}

/* ---------------- Footer ---------------- */
export function Footer() {
  return (
    <footer className="border-t border-edge py-14 text-center text-sm text-dim">
      <div className="wrap">
        <div className="mb-4 inline-flex items-center gap-2.5">
          <img src="icon.png" alt="" className="h-6 w-6" />
          <b className="text-base text-ink">persona</b>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-2.5 gap-y-1 text-sub">
          <a href={links.repo} className="inline-flex items-center gap-1.5 hover:text-lime">
            <GithubIcon className="h-4 w-4" /> GitHub
          </a>
          <span>·</span>
          <a href={links.releases} className="hover:text-lime">Releases</a>
          <span>·</span>
          <a href={links.issues} className="hover:text-lime">Issues</a>
          <span>·</span>
          <a href={links.security} className="hover:text-lime">Security</a>
        </div>
        <p className="mt-4 text-[13px]">MIT licensed · built by the persona contributors</p>
      </div>
    </footer>
  )
}
