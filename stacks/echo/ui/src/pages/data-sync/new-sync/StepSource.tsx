// Step 1: the Oracle connection and the table to replicate, plus Analyze.
import axios from 'axios'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, Check, KeyRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { BusyButton, EmptyState, Note } from '@/components/relay'
import { ChoiceCard } from '@/components/wizard/ChoiceCard'
import { TONE_CLASS } from '@/lib/statusVocabulary'
import { cn, tableToId, suggestWatermarkCol, suggestWatermarkFallbackCol, suggestPkCols, suggestPartitionCol, suggestSoftDeleteCol } from '@/lib/utils'
import { useWizardStore } from '@/store/wizardStore'
import { useConnections } from '@/hooks/useConnections'
import { api } from '@/lib/api'
import { FieldHint } from './fields'

export function StepSource() {
  const { connection, setConnection, tableName, setTableName, analyzeResult, setAnalyzeResult, patch } = useWizardStore()
  const { data: connections, isLoading, isError: connectionsError, refetch: refetchConnections } = useConnections()
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pollStatus, setPollStatus] = useState<string | null>(null)

  async function handleAnalyze() {
    if (!connection || !tableName.trim()) return
    setAnalyzing(true)
    setError(null)
    setPollStatus(null)
    try {
      const table = tableName.trim()
      setPollStatus('Triggering analysis job...')
      const { run_id } = await api.analyzeTableAsync(table, connection.arn)

      let result = null
      let elapsed = 0
      const maxWait = 180000 // 3 minutes
      const pollInterval = 2000

      while (elapsed < maxWait) {
        setPollStatus(`Analysis running... (${Math.round(elapsed / 1000)}s)`)
        const status = await api.getAnalysisStatus(run_id, table)

        if (status.status === 'SUCCEEDED' && status.result) {
          result = status.result
          break
        } else if (status.status === 'FAILED') {
          throw new Error(`Analysis failed: ${status.glue_status || 'Unknown error'}`)
        }

        await new Promise(resolve => setTimeout(resolve, pollInterval))
        elapsed += pollInterval
      }

      if (!result) throw new Error('Analysis timed out after 3 minutes')

      setPollStatus(null)
      setAnalyzeResult(result)
      const watermarkCol = suggestWatermarkCol(result.columns)
      const watermarkFallbackCol = suggestWatermarkFallbackCol(result.columns, watermarkCol)
      const pkCols = suggestPkCols(result.columns)
      const partitionCol = suggestPartitionCol(result.columns)
      const softDeleteCol = suggestSoftDeleteCol(result.columns)
      patch({
        watermarkCol,
        watermarkFallbackCol,
        partitionCol,
        extractMode: watermarkCol ? 'incremental' : 'full',
        pkCols: pkCols.length ? pkCols : [],
        silverEnabled: pkCols.length > 0,
        softDeleteCol,
        jdbcLowerBound: result.min_pk ?? null,
        jdbcUpperBound: result.max_pk ?? null,
        minRowsExpected: result.row_count ? Math.floor(result.row_count * 0.9) : null,
        notNullCols: result.columns.filter(c => !c.nullable).map(c => c.name),
        pipelineId: tableToId(result.table),
      })
    } catch (e: unknown) {
      const serverMsg = axios.isAxiosError(e) ? e.response?.data?.error : undefined
      setError(serverMsg ?? (e instanceof Error ? e.message : 'Analysis failed'))
      setPollStatus(null)
    } finally {
      setAnalyzing(false)
    }
  }

  const analyzed = !!analyzeResult && analyzeResult.table === tableName.trim()

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label>Oracle connection</Label>
        <FieldHint>Choose the pre-registered Oracle credential to use for this sync.</FieldHint>
        {isLoading ? (
          <FieldHint>Loading connections...</FieldHint>
        ) : connections && connections.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-3">
            {connections.map(c => (
              <ChoiceCard
                key={c.arn}
                selected={connection?.arn === c.arn}
                onSelect={() => setConnection(c)}
                title={c.name}
              >
                <span className="mt-0.5 block break-all font-mono text-label text-ink-mute">{c.arn}</span>
                {c.description && <span className="mt-0.5 block text-meta text-ink-mute">{c.description}</span>}
              </ChoiceCard>
            ))}
          </div>
        ) : (
          <div className="rounded-card border border-dashed border-paper-line">
            {connectionsError ? (
              <EmptyState
                icon={AlertCircle}
                title="Couldn't load connections"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => void refetchConnections()}>
                    Retry
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={KeyRound}
                title="No connections registered yet."
                action={
                  <Button asChild variant="outline" size="sm">
                    <Link to="/settings/connections">Open Connections</Link>
                  </Button>
                }
              >
                Go to Connections to add an Oracle credential first.
              </EmptyState>
            )}
          </div>
        )}
        <FieldHint>Connections are managed in Settings › Connections.</FieldHint>
      </div>

      <div className="space-y-2">
        <Label htmlFor="new-sync-table">Table name</Label>
        <div className="relative">
          <Input
            id="new-sync-table"
            placeholder="SCHEMA.TABLE_NAME"
            value={tableName}
            onChange={e => setTableName(e.target.value.toUpperCase())}
            className={cn('font-mono', analyzed && 'pr-64')}
          />
          {analyzed && (
            <span
              className={cn(
                'absolute right-2 top-1/2 inline-flex -translate-y-1/2 items-center gap-1 rounded-full px-2.5 py-0.5 text-label font-bold',
                TONE_CLASS.ok,
              )}
            >
              <Check size={11} strokeWidth={3} aria-hidden="true" />
              Analyzed · {analyzeResult.row_count.toLocaleString()} rows · {analyzeResult.columns.length} columns
            </span>
          )}
        </div>
        <FieldHint>
          Include the schema prefix (e.g. <code className="rounded bg-accent-soft px-1 text-label">INV.MTL_SYSTEM_ITEMS_B</code>).
          We'll connect to Oracle and discover the schema automatically.
        </FieldHint>
      </div>

      {error && <Note tone="fail">{error}</Note>}
      {analyzing && (
        <Note>
          {pollStatus ? 'Async analysis in progress. Results will appear shortly.' : 'Triggering analysis...'}
        </Note>
      )}

      <BusyButton
        type="button"
        variant="outline"
        onClick={handleAnalyze}
        busy={analyzing}
        disabled={!tableName.trim() || !connection}
      >
        {analyzing ? pollStatus || 'Analyzing...' : analyzed ? 'Re-analyze' : 'Analyze Table'}
      </BusyButton>
    </div>
  )
}
