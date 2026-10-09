/** S3 bucket name helpers for the Echo stack (echo-{env}-*), except gold: the
 *  gold layer is terraform-gold's and carries the standard uif-echo-{env}-*
 *  name in every environment (ADR-0018). */

export function echoBronzeBucket(env: string): string {
  return `echo-${env}-bronze`
}

export function echoSilverBucket(env: string): string {
  return `echo-${env}-silver`
}

export function echoGoldBucket(env: string): string {
  return `uif-echo-${env}-gold`
}

export function echoGoldArtifactsBucket(env: string): string {
  return `uif-echo-${env}-gold-artifacts`
}

/** Glue catalog database: silver is echo_{env}_{layer}_{domain}; gold carries
 *  the standard uif_echo_{env}_gold_{domain} name (ADR-0018). */
export function echoGlueDatabase(
  env: string,
  layer: 'silver' | 'gold',
  domain: string,
): string {
  return layer === 'gold'
    ? `uif_echo_${env}_gold_${domain}`
    : `echo_${env}_${layer}_${domain}`
}

/** GCS bucket names mirror the AWS echo-{env}-* convention. */
export function echoGcsBronzeBucket(env: string): string {
  return `echo-${env}-bronze`
}

export function echoGcsSilverBucket(env: string): string {
  return `echo-${env}-silver`
}

/** The Beacon panel (RELAY's export platform, UIFPE-484) — a sibling static
 *  app deployed under /m/beacon on this same distribution, hash-routed.
 *  Links to it are full page loads, never react-router navigations: the SPA's
 *  router has no /m/beacon routes and would swallow the path. */
export const BEACON_PANEL_PATH = '/m/beacon/index.html#/m/beacon'

/** Leave the SPA for the Beacon panel — a separate function so tests can mock
 *  the page-leave (jsdom cannot navigate). */
export function openBeaconPanel(): void {
  window.location.replace(BEACON_PANEL_PATH)
}

/** The transform-ui bundle - a sibling static app under /m/transform, hash-routed.
 *  Same rule as the Beacon panel: links to it are full page loads (plain <a>),
 *  never react-router links. */
export const TRANSFORM_UI_NEW_PATH = '/m/transform/index.html#/transform/new'
export const TRANSFORM_UI_SUBMISSIONS_PATH = '/m/transform/index.html#/transform/submissions'
