import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { FormField, TextField } from '../components/common/FormField';

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  authEnabled: boolean;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  isAuthenticated: false,
  isLoading: true,
  authEnabled: false,
  login: async () => false,
  logout: async () => {},
  checkAuth: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [authEnabled, setAuthEnabled] = useState(false);

  const checkAuth = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/status', {
        credentials: 'include',
      });
      const data = await response.json();
      setAuthEnabled(data.auth_enabled);
      setIsAuthenticated(data.auth_enabled ? data.logged_in : true);
    } catch {
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const login = useCallback(async (password: string): Promise<boolean> => {
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        credentials: 'include',
      });

      const ok = response.ok;
      setIsAuthenticated(ok);
      return ok;
    } catch (err) {
      console.error('[Auth] Login error:', err);
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
    } finally {
      setIsAuthenticated(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  return (
    <AuthContext.Provider value={{ isAuthenticated, isLoading, authEnabled, login, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[var(--bg)]">
        <div className="text-center">
          <div className="thinking">
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
          </div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return <>{children}</>;
}

function LoginPage() {
  const { login } = useAuth();
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    const success = await login(password);
    if (!success) {
      setError('Invalid password');
    }
    setLoading(false);
  };

  return (
    <div className="flex h-screen items-center justify-center bg-[var(--bg)]">
      <form onSubmit={handleSubmit} className="w-full max-w-sm p-8">
        <div className="text-center mb-8">
          <div className="empty-logo mx-auto mb-4">
            <svg
              width="72"
              height="72"
              viewBox="0 0 24 24"
              fill="none"
              style={{ stroke: 'var(--brand-primary)' }}
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="m8 3 4 8 5-5 5 15H2L8 3z" />
              <path d="M4.14 15.08c2.62-1.57 5.24-1.43 7.86.42 2.74 1.94 5.49 2 8.23.19" />
            </svg>
          </div>
          <h1 className="text-2xl font-medium text-[var(--text)]">Leap Aide</h1>
        </div>
        <FormField label="Password" className="mb-4" error={error}>
          <TextField
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
        </FormField>
        <button
          type="submit"
          disabled={loading || !password}
          className="btn-filled w-full"
        >
          {loading ? 'Logging in...' : 'Login'}
        </button>
      </form>
    </div>
  );
}
