import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  isAuthenticated: false,
  isLoading: true,
  login: async () => false,
  logout: async () => {},
  checkAuth: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/status', {
        credentials: 'include',
      });
      const data = await response.json();
      setIsAuthenticated(data.auth_enabled ? data.logged_in : true);
    } catch {
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const login = useCallback(async (password: string): Promise<boolean> => {
    try {
      console.log('[Auth] Login attempt with password:', password);

      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        credentials: 'include',
      });

      const responseText = await response.text();
      console.log('[Auth] Login response status:', response.status);
      console.log('[Auth] Login response body:', responseText);
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
    <AuthContext.Provider value={{ isAuthenticated, isLoading, login, logout, checkAuth }}>
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
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
              <defs>
                <linearGradient id="hermes-gold-login" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" style={{ stopColor: '#F5C542', stopOpacity: 1 }} />
                  <stop offset="100%" style={{ stopColor: '#D4961C', stopOpacity: 1 }} />
                </linearGradient>
              </defs>
              <rect x="30" y="10" width="4" height="46" rx="2" fill="url(#hermes-gold-login)" />
              <path d="M30 18 C24 14, 14 14, 10 18 C14 16, 22 16, 28 20" fill="#F5C542" opacity="0.9" />
              <path d="M30 22 C26 19, 18 19, 14 22 C18 20, 24 20, 28 24" fill="#D4961C" opacity="0.8" />
              <path d="M34 18 C40 14, 50 14, 54 18 C50 16, 42 16, 36 20" fill="#F5C542" opacity="0.9" />
              <path d="M34 22 C38 19, 46 19, 50 22 C46 20, 40 20, 36 24" fill="#D4961C" opacity="0.8" />
              <circle cx="32" cy="10" r="4" fill="#F5C542" />
              <circle cx="32" cy="10" r="2" fill="#FFF8E1" opacity="0.7" />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-[var(--text)]">Hermes WebUI</h1>
          <p className="text-sm text-[var(--muted)] mt-2">Enter password to continue</p>
        </div>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full px-4 py-3 rounded-lg mb-4"
          autoFocus
        />
        {error && <p className="text-sm text-[var(--accent)] mb-4">{error}</p>}
        <button
          type="submit"
          disabled={loading || !password}
          className="w-full py-3 rounded-lg bg-[var(--blue)] text-white font-semibold disabled:opacity-50"
        >
          {loading ? 'Logging in...' : 'Login'}
        </button>
      </form>
    </div>
  );
}
