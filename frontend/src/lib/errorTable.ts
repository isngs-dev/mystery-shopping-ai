import { useQuery } from '@tanstack/react-query'
import { api } from './api'

/** The fixed QC error table (Grammar 5 · Missed Instruction 15 · Contradiction 20 · Wrong Location 25). */
export interface ErrorSub { key: string; label: string; hint: string; manual_only: boolean }
export interface ErrorCat { key: string; label: string; points: number; score_after: number; severity: string; subtypes: ErrorSub[] }
export interface ErrorTable { total_score: number; categories: ErrorCat[] }

export function useErrorTable() {
  return useQuery({ queryKey: ['error-table'], queryFn: () => api.get<ErrorTable>('/api/qc/error-table'), staleTime: Infinity })
}
