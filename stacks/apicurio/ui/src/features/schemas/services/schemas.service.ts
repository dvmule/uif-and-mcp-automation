import { apiClient } from '@/services/api-client';
import type { NonCompliantResponse, SchemaDetailRead, SchemaListResponse, SchemaServicesRead } from '../types/schema.types';
import type { OverviewStats } from '../types/overview.types';

interface ListSchemasParams {
  limit?: number;
  offset?: number;
}

export async function listSchemas(params: ListSchemasParams = {}): Promise<SchemaListResponse> {
  const { limit = 100, offset = 0 } = params;
  const { data } = await apiClient.get<SchemaListResponse>('/schemas', {
    params: { limit, offset },
  });
  return data;
}

export async function getSchemaDetail(
  groupId: string,
  artifactId: string,
  version?: string,
): Promise<SchemaDetailRead> {
  const { data } = await apiClient.get<SchemaDetailRead>(
    `/schemas/${encodeURIComponent(groupId)}/${encodeURIComponent(artifactId)}`,
    { params: version ? { version } : undefined },
  );
  return data;
}

export async function getNonCompliant(): Promise<NonCompliantResponse> {
  const { data } = await apiClient.get<NonCompliantResponse>('/schemas/non-compliant');
  return data;
}

export async function refreshNonCompliant(): Promise<NonCompliantResponse> {
  const { data } = await apiClient.post<NonCompliantResponse>('/schemas/non-compliant/refresh');
  return data;
}

export async function getOverviewStats(): Promise<OverviewStats> {
  const { data } = await apiClient.get<OverviewStats>('/schemas/overview');
  return data;
}

export async function getSchemaServices(
  groupId: string,
  artifactId: string,
): Promise<SchemaServicesRead> {
  const { data } = await apiClient.get<SchemaServicesRead>(
    `/schemas/${encodeURIComponent(groupId)}/${encodeURIComponent(artifactId)}/services`,
  );
  return data;
}
