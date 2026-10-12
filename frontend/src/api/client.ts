const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

const GET_CACHE_TTL_MS = 60_000
const GET_CACHE_MAX_ITEMS = 200
const getCache = new Map<string, { expiresAt: number; promise: Promise<unknown> }>()

type AuthErrorHandler = () => void
let authErrorHandler: AuthErrorHandler | null = null

export function setAuthErrorHandler(handler: AuthErrorHandler | null): void {
  authErrorHandler = handler
}

function notifyAuthError(status: number): void {
  if (status === 401 && authErrorHandler) {
    getCache.clear()
    authErrorHandler()
  }
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: 'include' })
  if (!res.ok) {
    notifyAuthError(res.status)
    throw new Error(`GET ${path} -> HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export function getCachedJSON<T>(
  path: string,
  cacheTtlMs = GET_CACHE_TTL_MS,
): Promise<T> {
  const cached = getCache.get(path)
  if (cached && cached.expiresAt > Date.now()) {
    return cached.promise as Promise<T>
  }
  const promise = getJSON<T>(path).catch((err) => {
    getCache.delete(path)
    throw err
  })
  if (!getCache.has(path) && getCache.size >= GET_CACHE_MAX_ITEMS) {
    const oldestKey = getCache.keys().next().value as string | undefined
    if (oldestKey) getCache.delete(oldestKey)
  }
  getCache.set(path, { expiresAt: Date.now() + cacheTtlMs, promise })
  return promise
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    notifyAuthError(res.status)
    throw new Error(`POST ${path} -> HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    notifyAuthError(res.status)
    throw new Error(`PUT ${path} -> HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export async function deleteJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!res.ok) {
    notifyAuthError(res.status)
    throw new Error(`DELETE ${path} -> HTTP ${res.status}`)
  }
  return (await res.json()) as T
}
