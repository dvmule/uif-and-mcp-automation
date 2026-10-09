import { useQuery } from '@tanstack/react-query';
import { getSchemaDetail } from '../services/schemas.service';

interface UseSchemaDetailOptions {
  enabled?: boolean;
}

export function useSchemaDetail(
  groupId: string,
  artifactId: string,
  version?: string,
  options: UseSchemaDetailOptions = {},
) {
  return useQuery({
    queryKey: ['schema-detail', groupId, artifactId, version],
    queryFn: () => getSchemaDetail(groupId, artifactId, version),
    enabled: options.enabled !== false && Boolean(groupId && artifactId),
  });
}
