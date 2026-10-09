import { EnvBadge } from './EnvBadge'
import { SettingsButton } from './SettingsButton'
import { UserMenu } from './UserMenu'

/**
 * The purple top bar. `minimal` is the logo, name and environment only (the
 * sign-in page); otherwise the user menu and Settings sit on the right.
 */
export function AppHeader({ minimal = false }: { minimal?: boolean }) {
  return (
    <header className="flex h-16 shrink-0 items-center gap-4 bg-pearson-purple px-6 text-white">
      <img
        src="/pearson-logo-white.svg"
        alt="Pearson"
        className="h-[26px] w-auto"
        onError={(e) => {
          e.currentTarget.src = '/pearson-logo-white.png'
        }}
      />
      <div className="h-6 w-px bg-white/25" />
      <span className="whitespace-nowrap text-[15px] font-bold tracking-tight">Echo</span>
      <EnvBadge />
      {!minimal && (
        <div className="ml-auto flex items-center gap-3">
          <UserMenu />
          <SettingsButton />
        </div>
      )}
    </header>
  )
}
