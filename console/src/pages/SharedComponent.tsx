import { useMemo, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiClient, COMPONENT_SANDBOX, getComponentRenderUrl } from '../lib/api';
import { useFrameTheme } from '../components/chat/frameTheme';

/** A component shared "with signed-in users": rendered for whoever is signed
 * in, with their own permissions (capped to what the component declares). */
export function SharedComponent() {
  const { token } = useParams<{ token: string }>();
  const { data, error, isLoading } = useQuery({
    queryKey: ['shared-component', token],
    queryFn: () => apiClient.openSharedComponent(token!),
    enabled: !!token,
    retry: false,
    // Each open counts as a view; don't re-open on focus.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  const frameRef = useRef<HTMLIFrameElement>(null);
  const onFrameLoad = useFrameTheme(frameRef);
  const src = useMemo(
    () => (data ? getComponentRenderUrl(data.render_token, data.namespace, data.name, data.input) : ''),
    [data],
  );

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-page">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (error || !data) {
    const status = (error as { response?: { status?: number } } | null)?.response?.status;
    const message =
      status === 410 ? 'This link has expired or reached its view limit.' : 'This link is not available.';
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-page text-gray-400 text-sm">
        {message}
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-surface-page">
      <div className="px-4 py-2 border-b border-gray-800 text-sm text-gray-300">{data.title}</div>
      <iframe
        ref={frameRef}
        onLoad={onFrameLoad}
        src={src}
        sandbox={COMPONENT_SANDBOX}
        className="flex-1 w-full border-0"
        title={data.title}
      />
    </div>
  );
}
