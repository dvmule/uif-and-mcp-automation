import { useParams } from 'react-router-dom';
import { SchemaDetail } from '@/features/schemas/components/SchemaDetail';

export function SchemaDetailPage() {
  const { groupId, id } = useParams<{ groupId: string; id: string }>();
  return <SchemaDetail groupId={groupId!} artifactId={id!} />;
}
