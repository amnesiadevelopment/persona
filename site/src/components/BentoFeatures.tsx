import { motion } from 'framer-motion'
import { ShieldCheck, TerminalSquare, Smartphone, MonitorSmartphone } from 'lucide-react'
import { rise, stagger, viewportOnce } from '@/lib/motion'
import { cn } from '@/lib/cn'
import { FlagUS, FlagDE, FlagGB } from '@/lib/flags'
import TouchIdPrint from './TouchIdPrint'
import GeoLiveVisual from './GeoLiveVisual'
import InfinityFlow from './InfinityFlow'

function Eyebrow({ children }: { children: string }) {
  return (
    <div className="mb-3 inline-flex items-center gap-2 text-[13px] font-bold uppercase tracking-[0.15em] text-lime">
      <span className="h-0.5 w-5 rounded bg-lime" />
      {children}
    </div>
  )
}

const cardBase =
  'group relative overflow-hidden rounded-2xl border border-edge bg-panel transition-colors hover:border-lime-dim'

function IconRing({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-11 w-11 place-items-center rounded-full border border-edge2 text-sub">
      {children}
    </div>
  )
}

/* --- visual: Touch-ID-style fingerprint that fills from the bottom up --- */
function FingerprintVisual() {
  return (
    <div className="relative grid h-32 place-items-center">
      <TouchIdPrint size={104} />
    </div>
  )
}

/* --- visual: flag/persona chips floating --- */
function PersonaChips() {
  const chips = [
    { Flag: FlagUS, name: 'Marketing US' },
    { Flag: FlagDE, name: 'Shop EU' },
    { Flag: FlagGB, name: 'Research UK' },
  ]
  return (
    <div className="flex flex-col items-end gap-2.5">
      {chips.map(({ Flag, name }, i) => (
        <motion.div
          key={name}
          initial={{ opacity: 0, x: 20 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={viewportOnce}
          transition={{ duration: 0.4, delay: i * 0.1 }}
          className={cn(
            'inline-flex items-center gap-2 rounded-full border border-edge2 bg-bg2 px-3 py-1.5 text-[13px]',
            i === 1 && 'mr-6',
          )}
        >
          <Flag className="h-3.5 w-[18px] flex-none overflow-hidden rounded-[3px]" />
          <span className="text-sub">{name}</span>
          <span className="h-1.5 w-1.5 rounded-full bg-lime shadow-[0_0_6px] shadow-lime" />
        </motion.div>
      ))}
    </div>
  )
}

export default function BentoFeatures() {
  return (
    <section id="features" className="py-[88px]">
      <div className="wrap">
        <motion.div initial="hidden" whileInView="show" viewport={viewportOnce} variants={rise}>
          <Eyebrow>Features</Eyebrow>
          <h2 className="text-4xl font-extrabold tracking-[-0.03em]">
            Everything to keep accounts apart
          </h2>
          <p className="mt-3.5 max-w-2xl text-[17px] text-sub">
            Separate, human-like identities — fingerprint, network, storage and login, all handled
            per persona.
          </p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={viewportOnce}
          variants={stagger}
          className="mt-11 grid auto-rows-[minmax(0,auto)] grid-cols-1 gap-[18px] md:grid-cols-3"
        >
          {/* BIG: infinite personas */}
          <motion.div variants={rise} className={cn(cardBase, 'md:row-span-2 flex flex-col justify-between p-8')}>
            <div className="pointer-events-none absolute -left-10 -top-10 h-40 w-40 rounded-full bg-lime/10 blur-3xl" />
            <div className="relative flex flex-1 items-center justify-center py-6">
              <div className="text-center">
                <div className="flex justify-center">
                  <InfinityFlow width={150} />
                </div>
                <div className="mt-3 text-2xl font-bold">personas</div>
              </div>
            </div>
            <p className="relative text-sm text-sub">
              Spin up as many identities as you need — each isolated, each with its own fingerprint,
              proxy, cookies and storage.
            </p>
          </motion.div>

          {/* fingerprint scan */}
          <motion.div variants={rise} className={cn(cardBase, 'p-6')}>
            <FingerprintVisual />
            <h3 className="mt-4 text-[16.5px] font-semibold">Deterministic fingerprint</h3>
            <p className="mt-1.5 text-sm text-sub">
              Canvas, WebGL, audio, fonts and hardware spoofed from the profile name — stable, and
              unlinkable between personas.
            </p>
          </motion.div>

          {/* proxy waveform */}
          <motion.div variants={rise} className={cn(cardBase, 'p-6')}>
            <GeoLiveVisual />
            <h3 className="mt-4 text-[16.5px] font-semibold">Geo follows the proxy</h3>
            <p className="mt-1.5 text-sm text-sub">
              Locale, timezone and geolocation track the proxy's country. DNS and WebRTC stay inside
              the tunnel.
            </p>
          </motion.div>

          {/* WIDE: mTLS */}
          <motion.div variants={rise} className={cn(cardBase, 'md:col-span-2 flex items-center gap-6 p-6')}>
            <div className="flex-1">
              <IconRing>
                <ShieldCheck className="h-[22px] w-[22px] text-lime" strokeWidth={1.6} />
              </IconRing>
              <h3 className="mt-4 text-[16.5px] font-semibold">mTLS client certificates</h3>
              <p className="mt-1.5 max-w-md text-sm text-sub">
                Add a certificate and assign it to a profile. Presented only to its admin site —
                never anywhere else — and it never touches the OS store.
              </p>
            </div>
            <div className="hidden w-40 flex-none sm:block">
              <PersonaChips />
            </div>
          </motion.div>

          {/* automation */}
          <motion.div variants={rise} className={cn(cardBase, 'p-6')}>
            <IconRing>
              <TerminalSquare className="h-[22px] w-[22px] text-lime" strokeWidth={1.6} />
            </IconRing>
            <h3 className="mt-4 text-[16.5px] font-semibold">Local automation API</h3>
            <p className="mt-1.5 text-sm text-sub">
              An opt-in local server exposes profile management and a CDP port so an MCP client like
              Claude can drive personas. Off by default.
            </p>
            <div className="mt-4 rounded-lg border border-edge bg-bg2 px-3 py-2 font-mono text-[12px] text-sub">
              <span className="text-lime">POST</span> /profiles/:id/launch
            </div>
          </motion.div>

          {/* mobile personas */}
          <motion.div variants={rise} className={cn(cardBase, 'p-6')}>
            <IconRing>
              <Smartphone className="h-[22px] w-[22px] text-lime" strokeWidth={1.6} />
            </IconRing>
            <h3 className="mt-4 text-[16.5px] font-semibold">Mobile personas</h3>
            <p className="mt-1.5 text-sm text-sub">
              Android and iOS profiles from real device presets — user-agent, screen, touch and
              Client-Hints all match a real phone.
            </p>
            <div className="mt-4 flex gap-2">
              {['iPhone 15', 'Pixel 8', 'Galaxy S24'].map((d) => (
                <span key={d} className="rounded-full border border-edge2 bg-bg2 px-2.5 py-1 text-[11px] text-sub">
                  {d}
                </span>
              ))}
            </div>
          </motion.div>

          {/* resolution */}
          <motion.div variants={rise} className={cn(cardBase, 'p-6')}>
            <IconRing>
              <MonitorSmartphone className="h-[22px] w-[22px] text-lime" strokeWidth={1.6} />
            </IconRing>
            <h3 className="mt-4 text-[16.5px] font-semibold">Resolution &amp; HiDPI</h3>
            <p className="mt-1.5 text-sm text-sub">
              Report any screen resolution while the real window stays native and readable — no
              window-size tell.
            </p>
            <div className="mt-4 flex items-center gap-2 font-mono text-[12px] text-sub">
              <span className="rounded border border-edge2 px-2 py-1">1920×1080</span>
              <span className="text-lime">→</span>
              <span className="rounded border border-lime/30 px-2 py-1 text-lime">reported</span>
            </div>
          </motion.div>
        </motion.div>
      </div>
    </section>
  )
}
