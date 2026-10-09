import { useQuery } from '@tanstack/react-query';
import { listServices } from '../services/services.service';

export function useServiceList() {
  return useQuery({
    queryKey: ['services'],
    queryFn: listServices,
  });
}
