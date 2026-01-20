import { useEffect } from 'react';
import { useToast } from '../lib/toast-context';
import { apiClient } from '../lib/api';

export function APIErrorHandler({ children }: { children: React.ReactNode }) {
  const { showError } = useToast();

  useEffect(() => {
    // Set the error handler on the API client
    apiClient.setErrorHandler((message: string) => {
      showError(message, 7000); // Show errors for 7 seconds
    });
  }, [showError]);

  return <>{children}</>;
}
