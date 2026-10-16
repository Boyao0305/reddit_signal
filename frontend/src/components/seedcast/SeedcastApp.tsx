import { useEffect, useState } from 'react'
import { parseRoute } from '../../lib/seedcast/route'
import AboutPage from './AboutPage'
import ClusterPage from './ClusterPage'
import ComparePage from './ComparePage'
import LandscapePage from './LandscapePage'
import MoversPage from './MoversPage'

export default function SeedcastApp() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const onHash = () => {
      setRoute(parseRoute(window.location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  switch (route.name) {
    case 'movers':
      return <MoversPage />
    case 'compare':
      return <ComparePage />
    case 'cluster':
      return <ClusterPage id={route.id} />
    case 'about':
      return <AboutPage />
    default:
      return <LandscapePage />
  }
}
