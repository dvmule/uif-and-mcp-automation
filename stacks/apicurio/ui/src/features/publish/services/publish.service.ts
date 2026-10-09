import { apiClient } from '@/services/api-client';
import type { DirectSchemaRegisterRequest, SchemaRegistrationResult } from '../types/publish.types';

export async function registerSchema(
  groupId: string,
  artifactId: string,
  body: DirectSchemaRegisterRequest,
): Promise<SchemaRegistrationResult> {
  const { data } = await apiClient.post<SchemaRegistrationResult>(
    `/schemas/${encodeURIComponent(groupId)}/${encodeURIComponent(artifactId)}`,
    body,
  );
  return data;
}
