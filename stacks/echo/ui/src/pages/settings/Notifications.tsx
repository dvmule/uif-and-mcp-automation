import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BellRing, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, PageHeader, StatusBadge } from '@/components/relay'
import { DetailCard } from '@/components/data-sync/detail/DetailCard'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import {
  useAddAlertSubscriber,
  useAlertRules,
  useAlertSubscribers,
  useRemoveAlertSubscriber,
  useSetAlertSubscriber,
} from '@/hooks/useNotifications'
import { apiErrorMessage, isUndeployedRouteError } from '@/lib/apiError'
import { describeScope, groupRules, isValidEmail } from '@/lib/notifications'
import { alertRuleStatusView } from '@/lib/statusVocabulary'
import { relativeTime } from '@/lib/utils'
import type { AlertRuleStatus, AlertSubscriber } from '@/lib/types'

/** UIFPE-543: who is emailed when a gold alarm changes state. Subscribers are
 *  rows in the gold alert-subscribers table - self-service per table from a
 *  gold entity's detail page, and all of them here. The rules are the
 *  CloudWatch alarms themselves, live and read-only: they are Terraform's. */
export function Notifications() {
  const subs = useAlertSubscribers()
  const rules = useAlertRules()
  const { data: golds } = useGoldPipelines()
  const add = useAddAlertSubscriber()
  const set = useSetAlertSubscriber()
  const remove = useRemoveAlertSubscriber()

  const [email, setEmail] = useState('')
  const [chosen, setChosen] = useState<string[]>([])   // empty = everything
  const [message, setMessage] = useState<string | null>(null)

  const undeployed = isUndeployedRouteError(subs.error)
  const entities = (golds ?? []).map(g => g.gold_id).sort()

  function submit() {
    const address = email.trim().toLowerCase()
    if (!isValidEmail(address)) { setMessage('Enter a valid email address.'); return }
    setMessage(null)
    add.mutate({ email: address, goldIds: chosen.length ? chosen : undefined }, {
      onSuccess: () => { setEmail(''); setChosen([]); setMessage(`${address} added.`) },
      onError: e => setMessage(apiErrorMessage(e, 'Could not add the address.')),
    })
  }

  function toggleEntity(id: string) {
    setChosen(c => (c.includes(id) ? c.filter(x => x !== id) : [...c, id].sort()))
  }

  return (
    <div>
      <PageHeader
        title="Notifications"
        info="Who is emailed when a gold table's alarms fire."
      />

      {undeployed ? (
        <div className="rounded-card border border-line bg-paper-card shadow-card">
          <EmptyState icon={BellRing}>Email notifications are not available in this environment yet.</EmptyState>
        </div>
      ) : (
        <>
          <DetailCard
            title="Subscribers"
            aside={subs.data && `${subs.data.total} ${subs.data.total === 1 ? 'address' : 'addresses'}`}
          >
            <div className="space-y-3">
              {subs.isLoading && <Skeleton className="h-16 rounded-card" />}
              {subs.error && !undeployed && (
                <p className="text-body text-signal-fail">{apiErrorMessage(subs.error, 'Could not load subscribers.')}</p>
              )}
              {subs.data?.items.map(row => (
                <SubscriberRow
                  key={row.email}
                  row={row}
                  onToggle={enabled => set.mutate({ email: row.email, patch: { enabled } })}
                  onRemove={() => remove.mutate(row.email)}
                  busy={set.isPending || remove.isPending}
                />
              ))}
              {subs.data && subs.data.items.length === 0 && (
                <p className="py-4 text-center text-body text-ink-mute">Nobody is subscribed yet.</p>
              )}

              <form
                className="space-y-3 border-t border-line pt-3"
                noValidate
                onSubmit={e => { e.preventDefault(); submit() }}
              >
                <div className="flex gap-2">
                  <Input
                    type="email"
                    aria-label="Email address to add"
                    placeholder="name@pearson.com"
                    value={email}
                    onChange={e => { setEmail(e.target.value); setMessage(null) }}
                  />
                  <Button type="submit" size="sm" disabled={add.isPending || !email.trim()}>
                    <Plus size={14} className="mr-1" /> Add
                  </Button>
                </div>
                <div>
                  <Label className="text-meta text-ink-mute">Tables (none ticked = every table plus platform alerts)</Label>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {entities.map(id => (
                      <label key={id} className="inline-flex items-center gap-1.5 text-body">
                        <input type="checkbox" checked={chosen.includes(id)} onChange={() => toggleEntity(id)} className="accent-accent" />
                        {id}
                      </label>
                    ))}
                    {entities.length === 0 && <span className="text-meta text-ink-mute">No gold tables found.</span>}
                  </div>
                </div>
                {message && <p className="text-meta text-ink-mute" role="status">{message}</p>}
              </form>
            </div>
          </DetailCard>

          <DetailCard title="Rules" aside="read-only, live from CloudWatch">
            <div className="space-y-4">
              {rules.isLoading && <Skeleton className="h-24 rounded-card" />}
              {rules.error && (
                <p className="text-body text-ink-mute">
                  {isUndeployedRouteError(rules.error) ? 'Rules are not available in this environment yet.' : apiErrorMessage(rules.error, 'Could not load rules.')}
                </p>
              )}
              {rules.data && groupRules(rules.data.items).map(group => (
                <div key={group.key}>
                  <p className="mb-1 text-label font-semibold uppercase tracking-wider text-ink-mute">{group.label}</p>
                  <div className="space-y-1">
                    {group.rules.map(rule => <RuleRow key={rule.name} rule={rule} />)}
                  </div>
                </div>
              ))}
              {rules.data && rules.data.items.length === 0 && (
                <p className="py-4 text-center text-body text-ink-mute">No gold alarms in this environment.</p>
              )}
              <p className="border-t border-line pt-2 text-meta text-ink-mute">
                Rules are defined in Terraform (one set per gold table) and cannot be edited here. To see what a table's alarms
                mean, open it under <Link className="font-semibold text-accent hover:underline" to="/m/data-sync/gold">Transform</Link>.
              </p>
            </div>
          </DetailCard>
        </>
      )}
    </div>
  )
}

function SubscriberRow({ row, onToggle, onRemove, busy }: {
  row: AlertSubscriber; onToggle: (enabled: boolean) => void; onRemove: () => void; busy: boolean
}) {
  return (
    <div className="flex items-center justify-between rounded-brand border border-line p-3" data-testid={`subscriber-${row.email}`}>
      <div className="flex min-w-0 items-center gap-3">
        <Switch checked={row.enabled} onCheckedChange={onToggle} disabled={busy} aria-label={`Emails to ${row.email}`} />
        <div className="min-w-0">
          <p className={`truncate text-body font-semibold ${row.enabled ? 'text-ink' : 'text-ink-mute line-through'}`}>{row.email}</p>
          <p className="text-meta text-ink-mute">
            {describeScope(row)}
            {row.added_at && <> · added {relativeTime(row.added_at)}{row.added_by ? ` by ${row.added_by}` : ''}</>}
          </p>
        </div>
      </div>
      <Button
        variant="ghost" size="sm" className="h-7 w-7 p-0 text-ink-mute hover:text-signal-fail"
        aria-label={`Remove ${row.email}`} onClick={onRemove} disabled={busy}
      >
        <Trash2 size={14} />
      </Button>
    </div>
  )
}

function RuleRow({ rule }: { rule: AlertRuleStatus }) {
  return (
    <div className="flex items-center justify-between rounded-brand border border-line px-3 py-2" title={rule.description ?? undefined}>
      <div className="min-w-0">
        <p className="text-body text-ink">{rule.condition}</p>
        {rule.state_updated_at && <p className="text-meta text-ink-mute">since {relativeTime(rule.state_updated_at)}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {!rule.emails && <Badge variant="secondary" className="text-xs">no email</Badge>}
        <StatusBadge view={alertRuleStatusView(rule.state)} size="sm" />
      </div>
    </div>
  )
}
