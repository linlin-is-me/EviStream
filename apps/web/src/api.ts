const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export type ApiError = {
  error_code: string
  message: string
  correlation_id: string
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init)
  const payload = await response.json() as T | ApiError
  if (!response.ok) {
    const error = payload as ApiError
    throw new Error(`${error.error_code ?? 'HTTP_ERROR'}: ${error.message ?? response.statusText}`)
  }
  return payload as T
}

export const terminalJobs = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED'])
