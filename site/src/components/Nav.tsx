import { useEffect, useState } from 'react'
import { cn } from '@/lib/cn'
import { links } from '@/lib/data'

const navLinks = [
  { label: 'Engines', href: '#engines' },
  { label: 'Features', href: '#features' },
  { label: 'Support', href: '#support' },
]

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <nav
      className={cn(
        'sticky top-0 z-50 border-b backdrop-blur-xl transition-colors',
        scrolled ? 'border-edge bg-bg/80' : 'border-transparent bg-bg/40',
      )}
    >
      <div className="wrap flex h-[62px] items-center gap-3">
        <span className="relative grid h-7 w-7 place-items-center">
          <span className="absolute inset-[-4px] animate-pulse rounded-[9px] bg-lime/40 blur-md" />
          <img src="icon.png" alt="" className="relative z-10 h-7 w-7" />
        </span>
        <span className="text-[17px] font-bold tracking-tight">persona</span>

        <div className="ml-auto flex items-center gap-6">
          {navLinks.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="hidden text-sm text-sub transition-colors hover:text-ink sm:block"
            >
              {l.label}
            </a>
          ))}
          <a href={links.repo} className="text-sm text-sub transition-colors hover:text-ink">
            GitHub
          </a>
          <a
            href={links.releases}
            className="rounded-lg bg-lime px-4 py-2 text-sm font-bold text-black transition-transform hover:-translate-y-0.5"
          >
            Download
          </a>
        </div>
      </div>
    </nav>
  )
}
