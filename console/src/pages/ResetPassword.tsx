import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { apiClient } from '../lib/api';
import { KeyRound, Loader2, CheckCircle2 } from 'lucide-react';

export function ResetPassword() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  // Pre-fill the token if it came in via ?token=... (from a reset link)
  useEffect(() => {
    const tokenFromUrl = searchParams.get('token');
    if (tokenFromUrl) setResetToken(tokenFromUrl);
  }, [searchParams]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);
    try {
      await apiClient.redeemPasswordReset({
        reset_token: resetToken,
        new_password: newPassword,
      });
      setDone(true);
      setTimeout(() => navigate('/login'), 2500);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Reset failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface-page flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <img src="/sinas-logo.svg" alt="sinas" className="h-16" />
          </div>
          <h1 className="text-xl font-semibold text-gray-100 mb-2">Reset password</h1>
          <p className="text-gray-400">Use the one-time link from your administrator</p>
        </div>

        <div className="bg-surface-1 rounded-2xl p-8 border border-line-soft">
          {done ? (
            <div className="text-center space-y-4">
              <CheckCircle2 className="w-12 h-12 text-green-400 mx-auto" />
              <p className="text-gray-100">Password updated. Redirecting to sign in…</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="reset_token" className="block text-sm font-medium text-gray-300 mb-2">
                  Reset token
                </label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                  <input
                    id="reset_token"
                    type="text"
                    value={resetToken}
                    onChange={(e) => setResetToken(e.target.value)}
                    placeholder="Paste the token your admin sent you"
                    required
                    className="w-full pl-10 pr-4 py-3 bg-surface-input border border-line rounded-lg text-gray-100 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent font-mono text-sm"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="new_password" className="block text-sm font-medium text-gray-300 mb-2">
                  New password
                </label>
                <input
                  id="new_password"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  className="w-full px-4 py-3 bg-surface-input border border-line rounded-lg text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                />
              </div>

              <div>
                <label htmlFor="confirm_password" className="block text-sm font-medium text-gray-300 mb-2">
                  Confirm new password
                </label>
                <input
                  id="confirm_password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  className="w-full px-4 py-3 bg-surface-input border border-line rounded-lg text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                />
              </div>

              {error && (
                <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loading || !resetToken || !newPassword || !confirmPassword}
                className="w-full btn btn-primary py-3 rounded-lg flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                    Setting password...
                  </>
                ) : (
                  'Set new password'
                )}
              </button>

              <div className="text-center">
                <Link to="/login" className="text-sm text-gray-400 hover:text-gray-200">
                  Back to sign in
                </Link>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
