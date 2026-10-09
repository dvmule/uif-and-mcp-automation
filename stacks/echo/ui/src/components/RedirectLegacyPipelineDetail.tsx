import { Navigate, useParams } from 'react-router-dom'

/** The pre-module /pipelines/:id URL, sent to the sync detail page. */
export function RedirectLegacyPipelineDetail() {
  const { id } = useParams<{ id: string }>()
  return <Navigate to={`/m/data-sync/syncs/${id ?? ''}`} replace />
}
