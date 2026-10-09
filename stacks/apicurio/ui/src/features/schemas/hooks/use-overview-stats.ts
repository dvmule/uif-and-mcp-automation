import { useQuery } from '@tanstack/react-query';
import { getOverviewStats } from '../services/schemas.service';
import type { OverviewStats } from '../types/overview.types';

export function useOverviewStats() {
  return useQuery<OverviewStats>({
    queryKey: ['schemas', 'overview'],
    queryFn: getOverviewStats,
    staleTime: 60_000,
  });
}
