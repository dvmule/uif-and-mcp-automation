import { create } from 'zustand'
import type { AnalyzeResult, Connection, DestinationCloud } from '@/lib/types'

export interface WizardState {
  step: number
  connection: Connection | null
  // GCP-native fields: only consulted when destinationCloud === 'gcp'.
  // bronzeGcsPath / silverGcsPath replace the AWS s3:// paths; gcpSecretArn
  // is the Secrets Manager ARN storing the GCP service-account JSON;
  // bqProject / bqDataset / bqLocation map to BigQuery (via BigLake).
  destinationCloud: DestinationCloud
  gcpSecretArn: string
  bronzeGcsPath: string
  silverGcsPath: string
  bqProject: string
  bqDataset: string
  bqLocation: string
  tableName: string
  analyzeResult: AnalyzeResult | null
  extractMode: 'full' | 'incremental'
  watermarkCol: string | null
  watermarkFallbackCol: string | null
  partitionCol: string | null
  jdbcLowerBound: number | null
  jdbcUpperBound: number | null
  jdbcPartitions: number
  silverEnabled: boolean
  pkCols: string[]
  softDeleteCol: string | null
  replicationEnabled: boolean
  gcsBucket: string
  gcsPrefix: string
  gcsSecretArn: string
  silverBqEnabled: boolean
  silverBqBucket: string
  silverBqPrefix: string
  silverBqSecretArn: string
  silverBqProject: string
  silverBqDataset: string
  silverBqLocation: string
  frequency: 'hourly' | 'daily' | 'weekly'
  notNullCols: string[]
  minRowsExpected: number | null
  workerType: 'G.1X' | 'G.2X' | 'G.4X'
  numWorkers: number
  pipelineId: string

  setStep: (step: number) => void
  setConnection: (c: Connection | null) => void
  setTableName: (t: string) => void
  setAnalyzeResult: (r: AnalyzeResult | null) => void
  patch: (partial: Partial<Omit<WizardState, 'setStep' | 'setConnection' | 'setTableName' | 'setAnalyzeResult' | 'patch' | 'reset'>>) => void
  reset: () => void
}

const defaults = {
  step: 0,
  connection: null,
  destinationCloud: 'aws' as DestinationCloud,
  gcpSecretArn: '',
  bronzeGcsPath: '',
  silverGcsPath: '',
  bqProject: '',
  bqDataset: '',
  bqLocation: 'EU',
  tableName: '',
  analyzeResult: null,
  extractMode: 'incremental' as const,
  watermarkCol: null,
  watermarkFallbackCol: null,
  partitionCol: null,
  jdbcLowerBound: null,
  jdbcUpperBound: null,
  jdbcPartitions: 8,
  silverEnabled: false,
  pkCols: [],
  softDeleteCol: null,
  replicationEnabled: false,
  gcsBucket: '',
  gcsPrefix: '',
  gcsSecretArn: '',
  silverBqEnabled: false,
  silverBqBucket: '',
  silverBqPrefix: '',
  silverBqSecretArn: '',
  silverBqProject: '',
  silverBqDataset: '',
  silverBqLocation: 'EU',
  frequency: 'daily' as const,
  notNullCols: [],
  minRowsExpected: null,
  workerType: 'G.1X' as const,
  numWorkers: 2,
  pipelineId: '',
}

export const useWizardStore = create<WizardState>()((set) => ({
  ...defaults,
  setStep: (step) => set({ step }),
  setConnection: (connection) => set({ connection }),
  setTableName: (tableName) => set({ tableName }),
  setAnalyzeResult: (analyzeResult) => set({ analyzeResult }),
  patch: (partial) => set(partial),
  reset: () => set(defaults),
}))
