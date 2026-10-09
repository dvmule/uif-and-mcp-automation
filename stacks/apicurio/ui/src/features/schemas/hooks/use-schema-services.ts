import { useQuery } from '@tanstack/react-query';
import { getSchemaServices } from '../services/schemas.service';

interface UseSchemaServicesOptions {
  enabled?: boolean;
}

export function useSchemaServices(
  groupId: string,
  artifactId: string,
  options: UseSchemaServicesOptions = {},
) {
  return useQuery({
    queryKey: ['schema-services', groupId, artifactId],
    queryFn: () => getSchemaServices(groupId, artifactId),
    enabled: options.enabled !== false && Boolean(groupId && artifactId),
  });
}
