import React from 'react'
import { motion, type Variants } from 'framer-motion'

const EASE_OUT = [0.16, 1, 0.3, 1] as const

interface SectionWithMockupProps {
  eyebrow?: React.ReactNode
  title: React.ReactNode
  description: React.ReactNode
  primary: React.ReactNode
  secondary: React.ReactNode
  reverseLayout?: boolean
}

// Parallax feature block adapted from the 21st.dev SectionWithMockup pattern:
// a primary card and an offset "secondary" card behind it that drift in
// opposite directions as the section scrolls into view. Cards render arbitrary
// content (we pass drawn engine mockups instead of images).
export default function SectionWithMockup({
  eyebrow,
  title,
  description,
  primary,
  secondary,
  reverseLayout = false,
}: SectionWithMockupProps) {
  const containerVariants: Variants = {
    hidden: {},
    visible: { transition: { staggerChildren: 0.2 } },
  }
  const itemVariants: Variants = {
    hidden: { opacity: 0, y: 50 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: EASE_OUT } },
  }

  const layoutClasses = reverseLayout
    ? 'md:grid-cols-2 md:grid-flow-col-dense'
    : 'md:grid-cols-2'
  const textOrderClass = reverseLayout ? 'md:col-start-2' : ''
  const imageOrderClass = reverseLayout ? 'md:col-start-1' : ''

  return (
    <div className="relative overflow-hidden py-10 md:py-14">
      <div className="wrap relative z-10">
        <motion.div
          className={`grid w-full grid-cols-1 items-center gap-16 md:gap-8 ${layoutClasses}`}
          variants={containerVariants}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, amount: 0.2 }}
        >
          {/* text */}
          <motion.div
            className={`mx-auto mt-6 flex max-w-[546px] flex-col items-start gap-4 md:mx-0 md:mt-0 ${textOrderClass}`}
            variants={itemVariants}
          >
            {eyebrow}
            <h2 className="text-3xl font-extrabold leading-tight tracking-[-0.03em] md:text-[40px]">
              {title}
            </h2>
            <p className="text-[15px] leading-6 text-sub">{description}</p>
          </motion.div>

          {/* mockup stack */}
          <motion.div
            className={`relative mx-auto mt-6 w-full max-w-[300px] md:mt-0 md:max-w-[471px] ${imageOrderClass}`}
            variants={itemVariants}
          >
            {/* offset secondary card */}
            <motion.div
              className="absolute z-0 h-[317px] w-[300px] rounded-[32px] md:h-[500px] md:w-[472px]"
              style={{
                top: reverseLayout ? 'auto' : '8%',
                bottom: reverseLayout ? '8%' : 'auto',
                left: reverseLayout ? 'auto' : '-10%',
                right: reverseLayout ? '-10%' : 'auto',
                filter: 'blur(1px)',
              }}
              initial={{ y: 0 }}
              whileInView={{ y: reverseLayout ? -24 : -34 }}
              transition={{ duration: 1.2, ease: EASE_OUT }}
              viewport={{ once: true, amount: 0.5 }}
            >
              {secondary}
            </motion.div>

            {/* primary card */}
            <motion.div
              className="relative z-10 h-[405px] w-full overflow-hidden rounded-[32px] border border-edge2 bg-white/[0.04] backdrop-blur-[15px] md:h-[560px]"
              initial={{ y: 0 }}
              whileInView={{ y: reverseLayout ? 22 : 30 }}
              transition={{ duration: 1.2, ease: EASE_OUT, delay: 0.1 }}
              viewport={{ once: true, amount: 0.5 }}
            >
              {primary}
            </motion.div>
          </motion.div>
        </motion.div>
      </div>

    </div>
  )
}
