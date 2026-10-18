import type { DataStatusInfo, Persona, RunRecord } from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail
    const msg = typeof detail === 'string' ? detail : detail?.violations ? `${detail.message}: ${detail.violations.join('; ')}` : res.statusText
    throw new Error(msg)
  }
  return res.json()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  personas: () => req<Persona[]>('/personas'),
  run: (body: { persona: Persona; rm_answers: Record<string, unknown>; disabled_sources: string[]; mevcut_dagilim?: Record<string, number> }) =>
    req<RunRecord>('/runs', json(body)),
  runs: () => req<{ run_id: string; persona_id: string; created_at: string; status: string; rm_decision?: string }[]>('/runs'),
  getRun: (id: string) => req<RunRecord>(`/runs/${id}`),
  decide: (id: string, body: { decision: string; rm_name: string; comment?: string; edited_text?: string }) =>
    req<RunRecord>(`/runs/${id}/decision`, json(body)),
  dataStatus: () => req<DataStatusInfo>('/data/status'),
  uploadTefas: (file: File, asOf?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    if (asOf) fd.append('as_of', asOf)
    return req<{ tur: string; satir: number; fon_sayisi: number; celiskiler: unknown[] }>('/data/tefas', { method: 'POST', body: fd })
  },
}
