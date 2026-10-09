import { useQuery } from '@tanstack/react-query';
import { getServicePortfolio } from '../services/services.service';

export function useServicePortfolio(urn: string) {
  return useQuery({
    queryKey: ['service-portfolio', urn],
    queryFn: () => getServicePortfolio(urn),
    enabled: Boolean(urn),
  });
}
