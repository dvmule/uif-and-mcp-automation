/**
 * TypeScript types mirroring backend/app/schemas/service.py.
 * Keep aligned with ServicePortfolioRead.
 */

import type { SchemaSummary } from '@/features/schemas';

export interface ServiceSummary {
  ci_id: string;
  urn: string;
  name: string;
  domain: string;
  produces_count: number;
  consumes_count: number;
  produces: string[];
  consumes: string[];
  app_id: string | null;
  app_name: string | null;
}

export interface ServiceDependency {
  ci_id: string;
  urn: string;
  name: string;
  domain: string;
  shared_schema_count: number;
}

export interface ServiceDependencies {
  upstream: ServiceDependency[];
  downstream: ServiceDependency[];
}

export interface ServicePortfolioRead {
  ci_id: string;
  urn: string;
  name: string;
  domain: string;
  produces: SchemaSummary[];
  consumes: SchemaSummary[];
  dependencies: ServiceDependencies;
  app_id: string | null;
  app_name: string | null;
}
