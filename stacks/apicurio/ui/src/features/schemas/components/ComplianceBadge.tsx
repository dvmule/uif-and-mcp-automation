import { Badge } from '@/components/ui/Badge';

interface ComplianceBadgeProps {
  passed: boolean | undefined;
  loading?: boolean;
  /** The compliance query failed, so this schema's state is unknown. Distinct
    * from `passed === undefined`, which means "not asked". A blank cell in a
    * compliance column reads as "fine", so say "unknown" out loud instead. */
  error?: boolean;
}

export function ComplianceBadge({ passed, loading, error }: ComplianceBadgeProps) {
  if (loading) {
    return <Badge tone="neutral">checking…</Badge>;
  }
  if (error) {
    return <Badge tone="neutral">unknown</Badge>;
  }
  if (passed === undefined) {
    return null;
  }
  return passed ? (
    <Badge tone="ok">compliant</Badge>
  ) : (
    <Badge tone="fail">non-compliant</Badge>
  );
}
