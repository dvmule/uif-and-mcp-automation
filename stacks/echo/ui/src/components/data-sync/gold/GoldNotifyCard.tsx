import { useState } from 'react'
import { BellRing } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiErrorMessage, isUndeployedRouteError } from '@/lib/apiError'
import { isValidEmail } from '@/lib/notifications'
import {
  useAddAlertSubscriber,
  useAlertSubscribers,
  useRemoveAlertSubscriber,
  useSetAlertSubscriber,
} from '@/hooks/useNotifications'
import type { AlertSubscriber } from '@/lib/types'
import { DetailCard } from '@/components/data-sync/detail/DetailCard'

interface Props {
  goldId: string
}

/** UIFPE-543, self-service: a business user who cares about THIS table types
 *  their address here and is emailed when its alarms change state (run failed,
 *  stale, too long). The row is theirs afterwards - Settings → Notifications
 *  shows everyone. A row without a table filter hears everything (the
 *  operator), so it is listed but managed from Settings, not from here. */
export function GoldNotifyCard({ goldId }: Props) {
  const { data, isLoading, error } = useAlertSubscribers()
  const add = useAddAlertSubscriber()
  const set = useSetAlertSubscriber()
  const remove = useRemoveAlertSubscriber()
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  const undeployed = isUndeployedRouteError(error)
  const rows = data?.items ?? []
  const forThisTable = rows.filter(r => r.enabled && (r.gold_ids === null || r.gold_ids.includes(goldId)))
  const typed = email.trim().toLowerCase()
  const existing = rows.find(r => r.email === typed)
  const alreadyOnThisTable = !!existing && existing.enabled && (existing.gold_ids === null || existing.gold_ids.includes(goldId))
  const pending = add.isPending || set.isPending || remove.isPending

  function subscribe() {
    if (!isValidEmail(typed)) { setMessage('Enter a valid email address.'); return }
    setMessage(null)
    add.mutate({ email: typed, goldIds: [goldId] }, {
      onSuccess: () => setMessage(`${typed} will be emailed about ${goldId}.`),
      onError: e => setMessage(apiErrorMessage(e, 'Could not subscribe.')),
    })
  }

  function unsubscribe(row: AlertSubscriber) {
    setMessage(null)
    if (row.gold_ids === null) {
      setMessage(`${row.email} hears about every table; change that in Settings → Notifications.`)
      return
    }
    const done = { onSuccess: () => setMessage(`${row.email} will no longer be emailed about ${goldId}.`),
                   onError: (e: unknown) => setMessage(apiErrorMessage(e, 'Could not unsubscribe.')) }
    if (row.gold_ids.length === 1) remove.mutate(row.email, done)          // last table → the row goes
    else set.mutate({ email: row.email, patch: { remove_gold_ids: [goldId] } }, done)
  }

  return (
    <DetailCard title="Notifications" aside={<BellRing size={14} className="text-ink-mute" aria-hidden />}>
      {undeployed ? (
        <p className="text-body text-ink-mute">
          Email notifications are not available in this environment yet.
        </p>
      ) : (
        <>
          <p className="mb-3 text-body text-ink">
            Get an email when a <span className="font-medium">{goldId}</span> run fails, goes stale or runs too long.
          </p>

          <form
            className="flex gap-2"
            noValidate
            onSubmit={e => { e.preventDefault(); if (alreadyOnThisTable && existing) unsubscribe(existing); else subscribe() }}
          >
            <Input
              type="email"
              aria-label="Your email address"
              placeholder="you@pearson.com"
              value={email}
              onChange={e => { setEmail(e.target.value); setMessage(null) }}
              disabled={pending}
            />
            <Button type="submit" size="sm" variant={alreadyOnThisTable ? 'outline' : 'default'} disabled={pending || !typed}>
              {alreadyOnThisTable ? 'Stop notifying me' : 'Notify me'}
            </Button>
          </form>
          {message && <p className="mt-2 text-meta text-ink-mute" role="status">{message}</p>}

          <div className="mt-3 border-t border-line pt-3">
            <p className="mb-1 text-label uppercase tracking-wide text-ink-mute">
              Emailed about this table {isLoading ? '' : `(${forThisTable.length})`}
            </p>
            {isLoading ? (
              <p className="text-meta text-ink-mute">Loading…</p>
            ) : forThisTable.length === 0 ? (
              <p className="text-meta text-ink-mute">Nobody yet.</p>
            ) : (
              <ul className="space-y-1">
                {forThisTable.map(r => (
                  <li key={r.email} className="flex items-center justify-between text-meta text-ink">
                    <span className="truncate" title={r.email}>{r.email}</span>
                    <span className="ml-2 shrink-0 text-[10px] uppercase tracking-wide text-ink-mute">
                      {r.gold_ids === null ? 'all tables' : `${r.gold_ids.length} table${r.gold_ids.length === 1 ? '' : 's'}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </DetailCard>
  )
}
