/** Mirrors DirectSchemaRegisterRequest in stacks/apicurio/src/app/schemas/service.py */
export interface DirectSchemaRegisterRequest {
  service_urn: string;
  content: string;
  type_override?: string | null;
  data_classification: 'public' | 'internal' | 'confidential' | 'restricted';
  pii_fields?: string[];
  lifecycle?: 'active' | 'deprecated';
}

/**
 * Mirrors SchemaRegistrationResult. Note `registered` — the endpoint returns
 * HTTP 200 with registered:false and an error string on failure, so a 200 is
 * not success.
 */
export interface SchemaRegistrationResult {
  topic: string;
  registered: boolean;
  schema_type: string | null;
  error: string | null;
}
