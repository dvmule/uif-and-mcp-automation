import { getEnvLabel } from '@/lib/envConfig'

/** Display-only environment identifier — not interactive. */
export function EnvBadge() {
  return (
    <span
      className="rounded-full bg-white/15 px-2 py-0.5 text-label font-semibold uppercase tracking-wide"
      aria-label={`Environment: ${getEnvLabel()}`}
    >
      {getEnvLabel()}
    </span>
  )
}
