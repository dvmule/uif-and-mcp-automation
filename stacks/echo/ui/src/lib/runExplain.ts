// "What happened" for a run, in plain words (UIFPE-681). Pure: no React.
//
// Only patterns we can name are explained. Anything else says so and points at
// the raw message; it never guesses at a cause.
import { isInFlightStatus } from './runLifecycle'
import { statusBucket, type RunRow } from './runsTable'
import { formatRows } from './utils'

export type ExplainTone = 'fail' | 'warn' | 'ok' | 'mute'

/** Tinted box classes per tone, shared by the run drawer and the Latest failure card. */
export const EXPLAIN_TONE: Record<ExplainTone, string> = {
  fail: 'border-signal-fail/30 bg-signal-fail/5',
  warn: 'border-amber/50 bg-amber/10',
  ok: 'border-signal-ok/30 bg-signal-ok/5',
  mute: 'border-line bg-paper-head',
}

/** The pattern name explainRun gives a missing table or view. */
export const MISSING_SOURCE_PATTERN = 'missing source'

export interface RunExplanation {
  headline: string
  detail: string
  /** The rule that matched, shown small for the technical reader; null when none did. */
  pattern: string | null
  tone: ExplainTone
}

const CREDENTIALS = /ORA-01017|invalid username\/password/i
const QUALITY = /quality/i
const TIMEOUT = /timeout|timed out/i
const MISSING_SOURCE = /table or view not found|does not exist/i

function message(row: RunRow): string {
  return (row.run.error_message ?? '').trim()
}

export function explainRun(row: RunRow): RunExplanation {
  const text = message(row)
  const bucket = statusBucket(row.status)

  if (isInFlightStatus(row.status)) {
    return {
      headline: 'This run is still going.',
      detail: 'Numbers and the outcome appear here when it finishes. The page refreshes on its own.',
      pattern: 'running',
      tone: 'mute',
    }
  }

  if (bucket === 'SKIPPED') {
    return row.layer === 'gold'
      ? {
          headline: 'Skipped on purpose: no new silver changes.',
          detail: 'The tables this one reads had nothing new since its last run, so there was nothing to rebuild.',
          pattern: 'skipped',
          tone: 'mute',
        }
      : {
          headline: 'Skipped on purpose.',
          detail: 'The scheduler chose not to run this one. It is not an error.',
          pattern: 'skipped',
          tone: 'mute',
        }
  }

  if (bucket === 'FAILED' || bucket === 'WARNING') {
    if (CREDENTIALS.test(text)) {
      return {
        headline: "The source database refused the sync's login.",
        detail:
          'Oracle rejected the credentials this sync uses, so nothing was read. Later runs fail the same way until the secret is fixed.',
        pattern: 'ORA-01017',
        tone: 'fail',
      }
    }
    if ((row.run.rows_quality_failed ?? 0) > 0 || QUALITY.test(text)) {
      return {
        headline: 'The quality gate stopped this run.',
        detail: 'Rows failed the quality checks set for this table, so the run did not go through cleanly.',
        pattern: 'quality gate',
        tone: bucket === 'WARNING' ? 'warn' : 'fail',
      }
    }
    if (TIMEOUT.test(text)) {
      return {
        headline: 'The run took too long and was cut off.',
        detail: 'It hit its time limit before it finished, so what it had done was not completed.',
        pattern: 'timeout',
        tone: 'fail',
      }
    }
    if (MISSING_SOURCE.test(text)) {
      return {
        headline: 'A table this run needs was not found.',
        detail: 'The source table or view is missing or was renamed, so the run could not read it.',
        pattern: MISSING_SOURCE_PATTERN,
        tone: 'fail',
      }
    }
    if ((row.status ?? '').toUpperCase() === 'STOPPED') {
      return {
        headline: 'The run was stopped before it finished.',
        detail: 'Someone or something stopped it. See technical detail below if a reason was recorded.',
        pattern: null,
        tone: 'warn',
      }
    }
    if (bucket === 'WARNING') {
      return {
        headline: 'Finished with a warning.',
        detail: 'See technical detail below.',
        pattern: null,
        tone: 'warn',
      }
    }
    return {
      headline: 'Unrecognised error',
      detail: 'See technical detail below.',
      pattern: null,
      tone: 'fail',
    }
  }

  // Succeeded
  if ((row.run.rows_quality_failed ?? 0) > 0) {
    return {
      headline: 'Finished, but some rows failed the quality checks.',
      detail: `${formatRows(row.run.rows_quality_failed)} rows did not pass. See Results below.`,
      pattern: 'quality gate',
      tone: 'warn',
    }
  }
  if (row.rowsRead === 0) {
    return {
      headline: 'Nothing new to read.',
      detail: 'The source had no rows since the last run, so nothing was written. This is normal.',
      pattern: 'no new rows',
      tone: 'ok',
    }
  }
  if (row.layer === 'gold' && row.rowsWritten === 0) {
    return {
      headline: 'Completed. No rows changed.',
      detail: 'It ran cleanly; nothing in the silver tables it reads moved a row of this table.',
      pattern: null,
      tone: 'ok',
    }
  }
  const count = row.rowsWritten ?? row.rowsRead
  return {
    headline: count == null ? 'Completed.' : `Completed with ${formatRows(count)} rows.`,
    detail: 'It ran to the end without a problem.',
    pattern: null,
    tone: 'ok',
  }
}
