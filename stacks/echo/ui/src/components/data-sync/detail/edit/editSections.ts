// The seven edit sections, with the one-line aside each shows in its card header.
export const EDIT_SECTIONS: { key: string; label: string; aside: string }[] = [
  { key: 'identity',     label: 'Identity',     aside: 'who owns the sync and how it is tagged' },
  { key: 'extraction',   label: 'Extraction',   aside: 'how each run reads the table' },
  { key: 'glue-runtime', label: 'Glue runtime', aside: 'workers and timeout for each run' },
  { key: 'silver',       label: 'Silver',       aside: 'the deduplicated Iceberg table' },
  { key: 'quality',      label: 'Quality',      aside: 'gates a run must pass' },
  { key: 'reconcile',    label: 'Reconcile',    aside: 'finds rows deleted at the source' },
  { key: 'raw-json',     label: 'Raw JSON',     aside: 'read-only snapshot of the working draft' },
]

/** The predicate editor opens from Extraction; it is not one of the seven links. */
export const PREDICATES_SECTION = { key: 'predicates', label: 'JDBC predicates', aside: 'one Spark partition per predicate' }
