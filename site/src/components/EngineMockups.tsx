import { ChromiumIcon, FirefoxIcon } from '@/lib/brand-icons'

// Drawn browser-window mockups for the engine cards — a titlebar, an address
// bar, and a filled body: the engine mark, a spoof-status panel and tinted
// feature rows. No screenshots; everything is on-brand CSS and reads as a real
// profile window rather than an empty frame.

type Tone = {
  accent: string
  soft: string
  Icon: (p: { className?: string }) => React.ReactNode
  host: string
  rows: { label: string; value: string }[]
}

const CHROMIUM: Tone = {
  accent: '#7ea8ff',
  soft: 'rgba(80,140,255,0.16)',
  Icon: ChromiumIcon,
  host: 'Personium',
  rows: [
    { label: 'Canvas', value: 'noised' },
    { label: 'WebGL vendor', value: 'spoofed' },
    { label: 'Audio context', value: 'per-seed' },
    { label: 'Platform', value: 'Win32' },
  ],
}
const FIREFOX: Tone = {
  accent: '#ffab63',
  soft: 'rgba(255,140,40,0.16)',
  Icon: FirefoxIcon,
  host: 'patched Firefox 150+',
  rows: [
    { label: 'Spoof layer', value: 'C++ core' },
    { label: 'CDP', value: 'absent' },
    { label: 'navigator.webdriver', value: 'false' },
    { label: 'resistFingerprinting', value: 'on' },
  ],
}

function Window({ tone }: { tone: Tone }) {
  const { accent, soft, Icon, host, rows } = tone
  return (
    <div className="flex h-full flex-col">
      {/* titlebar */}
      <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.03] px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        <div className="mx-auto flex items-center gap-2 rounded-md bg-black/40 px-3 py-1 text-[11px] text-sub">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: accent }} />
          {host}
        </div>
      </div>

      {/* body */}
      <div className="relative flex-1 p-5">
        <div
          className="absolute inset-0 opacity-70"
          style={{ background: `radial-gradient(120% 70% at 50% 0%, ${soft}, transparent 65%)` }}
        />

        {/* engine header */}
        <div className="relative flex items-center gap-3">
          <div
            className="grid h-12 w-12 place-items-center rounded-2xl"
            style={{ background: soft, boxShadow: `0 0 30px ${soft}` }}
          >
            <Icon className="h-7 w-7" />
          </div>
          <div>
            <div className="text-sm font-semibold text-ink">{host}</div>
            <div className="flex items-center gap-1.5 text-[11px]" style={{ color: accent }}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: accent }} />
              fingerprint active
            </div>
          </div>
        </div>

        {/* spoof-status rows */}
        <div className="relative mt-5 space-y-2">
          {rows.map((r) => (
            <div
              key={r.label}
              className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2 text-[12px]"
            >
              <span className="text-sub">{r.label}</span>
              <span className="font-mono" style={{ color: accent }}>
                {r.value}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export const ChromiumPrimary = () => <Window tone={CHROMIUM} />
export const FirefoxPrimary = () => <Window tone={FIREFOX} />
