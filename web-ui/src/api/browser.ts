import { http } from '@/lib/http'

export interface BrowserStatus {
  enabled: boolean
  state?: 'needs_login' | 'ready' | 'verification_required'
  incident_id?: string
  reason?: string | null
  task_name?: string | null
  busy?: boolean
  browser_url?: string | null
  updated_at?: string
  notification?: string
}

export function getBrowserStatus(): Promise<BrowserStatus> {
  return http('/api/browser/status')
}

export function updateBrowserState(action: 'pause' | 'ready', incidentId: string, username: string, password: string): Promise<BrowserStatus> {
  const encoded = btoa(String.fromCharCode(...new TextEncoder().encode(`${username}:${password}`)))
  return http(`/api/browser/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Basic ${encoded}` },
    body: JSON.stringify({ incident_id: incidentId }),
  })
}
