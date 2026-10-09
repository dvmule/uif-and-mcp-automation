import { useQuery } from '@tanstack/react-query';
import { getNonCompliant } from '../services/schemas.service';
import type { NonCompliantResponse } from '../types/schema.types';

const NON_COMPLIANT_KEY = ['schemas', 'non-compliant'] as const;

export function useNonCompliant() {
  return useQuery<NonCompliantResponse>({
    queryKey: NON_COMPLIANT_KEY,
    queryFn: getNonCompliant,
  });
}
