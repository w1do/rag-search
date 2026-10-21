const TOKEN_KEY = "vector.auth.token";
const EXPIRES_KEY = "vector.auth.expires_at";

export function saveSession(token: string, expiresAt: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EXPIRES_KEY, expiresAt);
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EXPIRES_KEY);
}

export function getToken(): string | null {
  const token = localStorage.getItem(TOKEN_KEY);
  const expiresAt = localStorage.getItem(EXPIRES_KEY);
  if (!token || !expiresAt) {
    return null;
  }
  if (Date.parse(expiresAt) <= Date.now()) {
    clearSession();
    return null;
  }
  return token;
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}
