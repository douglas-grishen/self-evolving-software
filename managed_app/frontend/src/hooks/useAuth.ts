import { useState, useCallback, useEffect, useRef } from "react";

const API_BASE = "/api/v1/auth";
const TOKEN_KEY = "ses_admin_token";
const USER_KEY = "ses_admin_user";
const AUTH_INVALIDATED_EVENT = "ses:auth-invalidated";

export interface AdminUser {
  id: string;
  username: string;
  is_active: boolean;
  created_at: string;
  last_login: string | null;
}

export interface AuthState {
  token: string | null;
  user: AdminUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
}

interface AuthInvalidationDetail {
  message?: string;
}

function readStoredUser(): AdminUser | null {
  const stored = localStorage.getItem(USER_KEY);
  if (!stored) return null;

  try {
    return JSON.parse(stored) as AdminUser;
  } catch {
    localStorage.removeItem(USER_KEY);
    return null;
  }
}

function clearStoredAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function invalidateAuthSession(message?: string) {
  clearStoredAuth();
  window.dispatchEvent(
    new CustomEvent<AuthInvalidationDetail>(AUTH_INVALIDATED_EVENT, {
      detail: message ? { message } : {},
    }),
  );
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAuthToken();

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(input, { ...init, headers });

  if (response.status === 401) {
    invalidateAuthSession("Session expired. Sign in again.");
  }

  return response;
}

export function useAuth(): AuthState {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );
  const [user, setUser] = useState<AdminUser | null>(() => readStoredUser());
  const [isLoading, setIsLoading] = useState(() => !!localStorage.getItem(TOKEN_KEY));
  const [error, setError] = useState<string | null>(null);
  const validatedTokenRef = useRef<string | null>(null);

  useEffect(() => {
    const handleInvalidated = (event: Event) => {
      const detail =
        event instanceof CustomEvent
          ? (event as CustomEvent<AuthInvalidationDetail>).detail
          : undefined;
      validatedTokenRef.current = null;
      setToken(null);
      setUser(null);
      setIsLoading(false);
      setError(detail?.message ?? null);
    };

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== TOKEN_KEY && event.key !== USER_KEY) {
        return;
      }
      validatedTokenRef.current = null;
      setToken(localStorage.getItem(TOKEN_KEY));
      setUser(readStoredUser());
    };

    window.addEventListener(AUTH_INVALIDATED_EVENT, handleInvalidated as EventListener);
    window.addEventListener("storage", handleStorage);

    return () => {
      window.removeEventListener(AUTH_INVALIDATED_EVENT, handleInvalidated as EventListener);
      window.removeEventListener("storage", handleStorage);
    };
  }, []);

  useEffect(() => {
    if (!token) {
      validatedTokenRef.current = null;
      setUser(null);
      setIsLoading(false);
      return;
    }

    if (validatedTokenRef.current === token) {
      setIsLoading(false);
      return;
    }

    let active = true;
    setIsLoading(true);

    fetch(`${API_BASE}/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => {
          if (!res.ok) throw new Error("Session expired. Sign in again.");
          return res.json();
        })
        .then((data: AdminUser) => {
          if (!active) return;
          validatedTokenRef.current = token;
          setUser(data);
          localStorage.setItem(USER_KEY, JSON.stringify(data));
          setError(null);
        })
        .catch(() => {
          if (!active) return;
          invalidateAuthSession("Session expired. Sign in again.");
        })
        .finally(() => {
          if (active) {
            setIsLoading(false);
          }
        });

    return () => {
      active = false;
    };
  }, [token]);

  const login = useCallback(
    async (username: string, password: string): Promise<boolean> => {
      setIsLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setError(body.detail || "Invalid credentials");
          return false;
        }
        const { access_token } = await res.json();
        localStorage.setItem(TOKEN_KEY, access_token);
        setToken(access_token);

        // Fetch user info immediately so the desktop can render without waiting
        // for the background token validation pass.
        const meRes = await fetch(`${API_BASE}/me`, {
          headers: { Authorization: `Bearer ${access_token}` },
        });
        if (meRes.ok) {
          const userData: AdminUser = await meRes.json();
          validatedTokenRef.current = access_token;
          setUser(userData);
          localStorage.setItem(USER_KEY, JSON.stringify(userData));
        } else {
          invalidateAuthSession("Session expired. Sign in again.");
          setError("Session expired. Sign in again.");
          return false;
        }
        return true;
      } catch {
        setError("Network error");
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const logout = useCallback(() => {
    setError(null);
    invalidateAuthSession();
  }, []);

  return {
    token,
    user,
    isAuthenticated: !!token,
    isLoading,
    error,
    login,
    logout,
  };
}

/**
 * Helper: get the stored token for API calls that need auth.
 */
export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
