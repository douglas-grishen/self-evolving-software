import { useState, useCallback, useEffect } from "react";

const API_BASE = "/api/v1/auth";
const TOKEN_KEY = "ses_admin_token";
const USER_KEY = "ses_admin_user";

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

export function useAuth(): AuthState {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );
  const [user, setUser] = useState<AdminUser | null>(() => {
    const stored = localStorage.getItem(USER_KEY);
    return stored ? JSON.parse(stored) : null;
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Validate token on mount
  useEffect(() => {
    if (token && !user) {
      fetch(`${API_BASE}/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => {
          if (!res.ok) throw new Error("Token expired");
          return res.json();
        })
        .then((data: AdminUser) => {
          setUser(data);
          localStorage.setItem(USER_KEY, JSON.stringify(data));
        })
        .catch(() => {
          // Token invalid, clear everything
          setToken(null);
          setUser(null);
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(USER_KEY);
        });
    }
  }, [token, user]);

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

        // Fetch user info
        const meRes = await fetch(`${API_BASE}/me`, {
          headers: { Authorization: `Bearer ${access_token}` },
        });
        if (meRes.ok) {
          const userData: AdminUser = await meRes.json();
          setUser(userData);
          localStorage.setItem(USER_KEY, JSON.stringify(userData));
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
    setToken(null);
    setUser(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
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
