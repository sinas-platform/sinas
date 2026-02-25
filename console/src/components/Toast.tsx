import { X, AlertCircle, CheckCircle, Info, AlertTriangle } from 'lucide-react';
import { useEffect } from 'react';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface ToastItemProps {
  toast: Toast;
  onClose: (id: string) => void;
}

export function ToastItem({ toast, onClose }: ToastItemProps) {
  useEffect(() => {
    const duration = toast.duration || 5000;
    const timer = setTimeout(() => {
      onClose(toast.id);
    }, duration);

    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onClose]);

  const getIcon = () => {
    switch (toast.type) {
      case 'success':
        return <CheckCircle className="w-5 h-5 text-green-500" />;
      case 'error':
        return <AlertCircle className="w-5 h-5 text-red-500" />;
      case 'warning':
        return <AlertTriangle className="w-5 h-5 text-yellow-500" />;
      case 'info':
        return <Info className="w-5 h-5 text-blue-500" />;
    }
  };

  const getBackgroundClass = () => {
    switch (toast.type) {
      case 'success':
        return 'bg-green-900/20 border-green-800/30';
      case 'error':
        return 'bg-red-900/20 border-red-800/30';
      case 'warning':
        return 'bg-yellow-900/20 border-yellow-800/30';
      case 'info':
        return 'bg-blue-900/20 border-blue-800/30';
    }
  };

  const getTextClass = () => {
    switch (toast.type) {
      case 'success':
        return 'text-green-300';
      case 'error':
        return 'text-red-300';
      case 'warning':
        return 'text-yellow-300';
      case 'info':
        return 'text-blue-300';
    }
  };

  return (
    <div
      className={`flex items-start gap-3 p-4 rounded-lg border ${getBackgroundClass()} animate-slide-in-right`}
      style={{ minWidth: '300px', maxWidth: '500px' }}
    >
      {getIcon()}
      <p className={`flex-1 text-sm ${getTextClass()}`}>{toast.message}</p>
      <button
        onClick={() => onClose(toast.id)}
        className={`${getTextClass()} hover:opacity-70`}
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

interface ToastContainerProps {
  toasts: Toast[];
  onClose: (id: string) => void;
}

export function ToastContainer({ toasts, onClose }: ToastContainerProps) {
  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onClose={onClose} />
      ))}
    </div>
  );
}
