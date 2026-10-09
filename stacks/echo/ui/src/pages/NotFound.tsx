import { Link, useLocation } from 'react-router-dom'
import { MapPinOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/relay'

/** The catch-all inside the shell: says what was asked for and offers the three usual places. */
export function NotFound() {
  const { pathname } = useLocation()
  return (
    <div className="mx-auto max-w-[1180px] rounded-card border border-line bg-paper-card shadow-card">
      <EmptyState
        icon={MapPinOff}
        title="Page not found"
        headingLevel={1}
        action={
          <>
            <Button asChild>
              <Link to="/">Home</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/m/data-sync/syncs">Replicate</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/m/data-sync/gold">Transform</Link>
            </Button>
          </>
        }
      >
        There is nothing at <code className="font-mono text-ink">{pathname}</code>. It may have moved during the
        redesign.
      </EmptyState>
    </div>
  )
}

export default NotFound
