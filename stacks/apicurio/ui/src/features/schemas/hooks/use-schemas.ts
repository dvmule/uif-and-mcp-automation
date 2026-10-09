import { useQuery } from '@tanstack/react-query';
import { listSchemas } from '../services/schemas.service';

interface UseSchemasOptions {
  limit?: number;
  offset?: number;
}

export function useSchemas({ limit = 100, offset = 0 }: UseSchemasOptions = {}) {
  return useQuery({
    queryKey: ['schemas', { limit, offset }],
    queryFn: () => listSchemas({ limit, offset }),
  });
}
