import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth-context';
import { Mail, Lock, Loader2 } from 'lucide-react';

export function Login() {
  const [email, setEmail] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [step, setStep] = useState<'email' | 'otp'>('email');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const { login, verifyOTP } = useAuth();
  const navigate = useNavigate();

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const sessionId = await login(email);
      setSessionId(sessionId);
      setStep('otp');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to send OTP');
    } finally {
      setLoading(false);
    }
  };

  const handleOTPSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await verifyOTP(sessionId, otpCode);
      navigate('/');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Invalid OTP code');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#090909] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo and title */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <img src="/sinas-logo.svg" alt="sinas" className="h-16" />
          </div>
          <h1 className="text-xl font-semibold text-gray-100 mb-2">Management Console</h1>
          <p className="text-gray-400">Sovereign Infrastructure for Native Agentic Systems</p>
        </div>

        {/* Login card */}
        <div className="bg-[#161616] rounded-2xl p-8 border border-white/[0.06]">
          {step === 'email' ? (
            <>
              <div className="mb-6">
                <h2 className="text-2xl font-semibold text-gray-100 mb-2">Welcome back</h2>
                <p className="text-gray-400">Enter your email to receive a login code</p>
              </div>

              <form onSubmit={handleEmailSubmit} className="space-y-4">
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-2">
                    Email address
                  </label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                    <input
                      id="email"
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      required
                      className="w-full pl-10 pr-4 py-3 bg-[#111111] border border-white/10 rounded-lg text-gray-100 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                    />
                  </div>
                </div>

                {error && (
                  <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading || !email}
                  className="w-full btn btn-primary py-3 rounded-lg flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                      Sending code...
                    </>
                  ) : (
                    'Continue with email'
                  )}
                </button>
              </form>
            </>
          ) : (
            <>
              <div className="mb-6">
                <h2 className="text-2xl font-semibold text-gray-100 mb-2">Verify your email</h2>
                <p className="text-gray-400">
                  We sent a code to <span className="font-medium text-gray-100">{email}</span>
                </p>
              </div>

              <form onSubmit={handleOTPSubmit} className="space-y-4">
                <div>
                  <label htmlFor="otp" className="block text-sm font-medium text-gray-300 mb-2">
                    Verification code
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                    <input
                      id="otp"
                      type="text"
                      value={otpCode}
                      onChange={(e) => setOtpCode(e.target.value)}
                      placeholder="000000"
                      required
                      maxLength={6}
                      className="w-full pl-10 pr-4 py-3 bg-[#111111] border border-white/10 rounded-lg text-gray-100 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent text-center text-2xl font-mono tracking-widest"
                    />
                  </div>
                </div>

                {error && (
                  <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading || otpCode.length !== 6}
                  className="w-full btn btn-primary py-3 rounded-lg flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                      Verifying...
                    </>
                  ) : (
                    'Verify and continue'
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setStep('email');
                    setOtpCode('');
                    setError('');
                  }}
                  className="w-full text-sm text-gray-400 hover:text-gray-200"
                >
                  Use a different email
                </button>
              </form>
            </>
          )}
        </div>

        <p className="text-center text-sm text-gray-500 mt-6">
          Secure authentication powered by Sinas
        </p>
      </div>
    </div>
  );
}
