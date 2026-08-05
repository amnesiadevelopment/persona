import Nav from './components/Nav'
import Hero from './components/Hero'
import {
  Marquee,
  Engines,
  Scanner,
  DownloadSection,
  Support,
  Footer,
} from './components/Sections'
import BentoFeatures from './components/BentoFeatures'

export default function App() {
  return (
    <>
      <Nav />
      <Hero />
      <Marquee />
      <Engines />
      <BentoFeatures />
      <DownloadSection />
      <Scanner />
      <Support />
      <Footer />
    </>
  )
}
