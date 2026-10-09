// Loads the registered openapi.yaml and validates response bodies against its
// component schemas. OpenAPI 3.1 is a strict superset of JSON Schema 2020-12,
// so the very schemas the contract declares are the ones we validate against —
// no separate copy, no drift.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const openapiPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../openapi.yaml',
);

const doc = parseYaml(readFileSync(openapiPath, 'utf8')) as {
  components: { schemas: Record<string, unknown> };
};

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
// Register the whole document so internal `#/components/schemas/...` $refs resolve.
ajv.addSchema(doc, 'openapi.yaml');

// Returns [] when body matches the named component schema, else the errors.
export function schemaErrors(schemaName: string, body: unknown): string[] {
  const validate = ajv.getSchema(`openapi.yaml#/components/schemas/${schemaName}`);
  if (!validate) throw new Error(`schema ${schemaName} not found in openapi.yaml`);
  return validate(body) ? [] : (validate.errors ?? []).map((e) => `${e.instancePath} ${e.message}`);
}

// The set of paths the contract declares — a response to an undeclared path is a drift.
export function contractHasPath(templatePath: string): boolean {
  return Object.prototype.hasOwnProperty.call(doc as never, 'paths')
    ? Object.prototype.hasOwnProperty.call((doc as { paths: object }).paths, templatePath)
    : false;
}
