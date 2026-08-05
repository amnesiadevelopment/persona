import { motion } from 'framer-motion'

// Recreation of persona's startup splash: the fingerprint mark glowing green
// inside scanner corner-brackets, with a soft red neon beam sweeping down and
// back up over the print — looped forever.
const BOX = 208
const LOGO = 128
const BRACKET = 26
const BRACKET_W = 2.5
const RADIUS = 6
const BEAM_H = 76
const CORE_H = 4
const GREEN = '#97ca00'
const RED = '#ff3b3b'

// An L-shaped corner drawn with two borders on one element, so both arms share
// the exact same width and meet cleanly at the corner (with a small radius on
// the joint). Every corner is identical, just mirrored via which two sides get
// the border and which corner gets the radius.
function Bracket({ v, h }: { v: 'top' | 'bottom'; h: 'left' | 'right' }) {
  const border = `${BRACKET_W}px solid ${GREEN}`
  const style: React.CSSProperties = {
    width: BRACKET,
    height: BRACKET,
    [v]: 0,
    [h]: 0,
    borderTop: v === 'top' ? border : undefined,
    borderBottom: v === 'bottom' ? border : undefined,
    borderLeft: h === 'left' ? border : undefined,
    borderRight: h === 'right' ? border : undefined,
    // round only the outer corner where the two arms meet
    borderTopLeftRadius: v === 'top' && h === 'left' ? RADIUS : 0,
    borderTopRightRadius: v === 'top' && h === 'right' ? RADIUS : 0,
    borderBottomLeftRadius: v === 'bottom' && h === 'left' ? RADIUS : 0,
    borderBottomRightRadius: v === 'bottom' && h === 'right' ? RADIUS : 0,
    boxShadow: `0 0 8px ${GREEN}55`,
  }
  return <span className="pointer-events-none absolute" style={style} />
}

export default function FingerprintScan() {
  // beam travels from the logo's top edge to its bottom edge
  const travel = LOGO - CORE_H
  const beamTop = (BOX - LOGO) / 2 - (BEAM_H - CORE_H) / 2

  return (
    <div className="grid place-items-center" style={{ width: BOX, height: BOX }}>
      <div className="relative" style={{ width: BOX, height: BOX }}>
        {/* fingerprint mark. The PNG already carries its own rounded green
            frame, so we don't clip or re-round it (that mismatched the artwork
            radius and left black wedges in the corners). A soft glow sits behind
            it via a separate blurred layer so nothing bleeds over the edges. */}
        <div
          className="absolute -z-10 rounded-[28px] blur-2xl"
          style={{
            width: LOGO,
            height: LOGO,
            left: (BOX - LOGO) / 2,
            top: (BOX - LOGO) / 2,
            background: `${GREEN}44`,
          }}
        />
        <img
          src="icon.png"
          alt="persona fingerprint"
          width={LOGO}
          height={LOGO}
          className="absolute block"
          style={{ left: (BOX - LOGO) / 2, top: (BOX - LOGO) / 2 }}
        />

        {/* corner brackets */}
        <Bracket v="top" h="left" />
        <Bracket v="top" h="right" />
        <Bracket v="bottom" h="left" />
        <Bracket v="bottom" h="right" />

        {/* sweeping beam (haze band + hot core) */}
        <motion.div
          className="pointer-events-none absolute"
          style={{ width: LOGO, height: BEAM_H, left: (BOX - LOGO) / 2, top: beamTop }}
          initial={{ y: 0 }}
          animate={{ y: travel }}
          transition={{ duration: 1.1, repeat: Infinity, repeatType: 'reverse', ease: 'easeInOut' }}
        >
          {/* soft red haze */}
          <div
            className="absolute inset-0"
            style={{
              background: `linear-gradient(to bottom, transparent, ${RED}22 42%, ${RED}55 50%, ${RED}22 58%, transparent)`,
            }}
          />
          {/* hot white core with red bloom */}
          <div
            className="absolute rounded"
            style={{
              height: CORE_H,
              left: 0,
              right: 0,
              top: (BEAM_H - CORE_H) / 2,
              background: 'linear-gradient(to right, transparent, #fff 20%, #fff 80%, transparent)',
              boxShadow: `0 0 6px #fff, 0 0 14px ${RED}, 0 0 26px ${RED}cc`,
            }}
          />
        </motion.div>
      </div>
    </div>
  )
}
