import { useEffect } from 'react';
import { setErrorHandler } from './http';
import { useToast } from './toast';

/** Shows failed API requests as error toasts. Mount once inside ToastProvider. */
export function ApiErrorToasts() {
  const { showError } = useToast();
  useEffect(() => {
    setErrorHandler(showError);
    return () => setErrorHandler(null);
  }, [showError]);
  return null;
}
