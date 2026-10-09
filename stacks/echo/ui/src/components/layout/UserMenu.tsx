import { useNavigate } from 'react-router-dom'
import { ChevronDown, LogOut, User } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { logout } from '@/lib/auth'
import { useAuthStore } from '@/store/authStore'

/**
 * Signed-in user + sign out, for the AppShell header.
 *
 * Renders nothing when there is no session — the header is also used by
 * routes that render outside the guard in tests.
 */
export function UserMenu() {
  const navigate = useNavigate()
  const username = useAuthStore((s) => s.username)
  const clear = useAuthStore((s) => s.clear)

  if (!username) return null

  async function onSignOut() {
    // No-op under the interim scheme; becomes the IdP end-session redirect.
    await logout()
    clear()
    navigate('/login', { replace: true })
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded px-1.5 py-0.5 text-meta text-white/75 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={`Account: ${username}`}
        >
          <User size={14} strokeWidth={1.9} />
          <span className="max-w-[160px] truncate">{username}</span>
          <ChevronDown size={12} strokeWidth={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[200px] rounded-brand border border-line bg-paper-card p-1 shadow-card"
        >
          <DropdownMenu.Label className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-mute">
            Signed in as
          </DropdownMenu.Label>
          <div className="px-2 pb-1.5 text-sm text-ink truncate">{username}</div>
          <DropdownMenu.Separator className="my-1 h-px bg-line" />
          <DropdownMenu.Item
            onSelect={onSignOut}
            className="flex items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-[#f3f0ff]"
          >
            <LogOut size={16} strokeWidth={1.75} />
            <span>Sign out</span>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
