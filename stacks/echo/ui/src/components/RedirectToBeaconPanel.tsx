import { useEffect } from 'react'
import { openBeaconPanel } from '@/lib/echoPaths'

/** UIFPE-484: "Exports" is the Beacon panel — a sibling static app under
 *  /m/beacon, so leaving the SPA takes a full page load, not a <Navigate>. */
export function RedirectToBeaconPanel() {
  useEffect(() => { openBeaconPanel() }, [])
  return null
}
