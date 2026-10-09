export const FEATURE_FLAGS = {
  syncDetailMissionControl: true,
  resetWatermarkEnabled: false,
  reBootstrapEnabled: false,
  draftPersistenceEnabled: false,
} as const

export type FeatureFlag = keyof typeof FEATURE_FLAGS
