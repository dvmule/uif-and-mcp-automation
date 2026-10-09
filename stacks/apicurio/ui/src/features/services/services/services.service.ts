import { apiClient } from '@/services/api-client';
import type { ServicePortfolioRead, ServiceSummary } from '../types/service.types';

export async function listServices(): Promise<ServiceSummary[]> {
  const { data } = await apiClient.get<ServiceSummary[]>('/services');
  return data;
}

export async function getServicePortfolio(urn: string): Promise<ServicePortfolioRead> {
  const { data } = await apiClient.get<ServicePortfolioRead>(
    `/services/${encodeURIComponent(urn)}/schema-portfolio`,
  );
  return data;
}
