import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { AlertCircle, CheckCircle, Info, X } from 'lucide-react';

export type ToastType = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastContextValue {
  showToast: (message: string, type?: ToastType) => void;
  showError: (message: string) => void;
  showSuccess: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const STYLES: Record<ToastType, { box: string; text: string; icon: ReactNode }> = {
  success: { box: 'bg-green-900/20 border-green-800/30', text: 'text-green-300', icon: <CheckCircle className="w-5 h-5 text-green-500" aria-hidden="true" /> },
  error: { box: 'bg-red-900/20 border-red-800/30', text: 'text-red-300', icon: <AlertCircle className="w-5 h-5 text-red-500" aria-hidden="true" /> },
  info: { box: 'bg-blue-900/20 border-blue-800/30', text: 'text-blue-300', icon: <Info className="w-5 h-5 text-blue-500" aria-hidden="true" /> },
};

function Toast({ toast, onClose }: { toast: ToastItem; onClose: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onClose(toast.id), toast.type === 'error' ? 8000 : 4000);
    return () => clearTimeout(timer);
  }, [toast, onClose]);
  const s = STYLES[toast.type];
  return (
    <div role={toast.type === 'error' ? 'alert' : 'status'} className={`flex items-start gap-3 p-4 rounded-lg border bg-surface-1 ${s.box} animate-slide-in-right min-w-[300px] max-w-[500px]`}>
      {s.icon}
      <p className={`flex-1 text-sm whitespace-pre-line ${s.text}`}>{toast.message}</p>
      <button type="button" onClick={() => onClose(toast.id)} className={`${s.text} hover:opacity-70`} aria-label="Dismiss">
        <X className="w-4 h-4" aria-hidden="true" />
      </button>
    </div>
  );
}

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    setToasts((prev) => [...prev, { id: nextId++, type, message }]);
  }, []);
  const showError = useCallback((m: string) => showToast(m, 'error'), [showToast]);
  const showSuccess = useCallback((m: string) => showToast(m, 'success'), [showToast]);
  const remove = useCallback((id: number) => setToasts((prev) => prev.filter((t) => t.id !== id)), []);
  return (
    <ToastContext.Provider value={{ showToast, showError, showSuccess }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2">
        {toasts.map((t) => <Toast key={t.id} toast={t} onClose={remove} />)}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}
