/** Where to go after signing in: the console page that sent you to the login
 * (a shared link, for one). Only paths inside the console are followed. */
export function returnPath(state: unknown): string {
  const from = (state as { from?: string } | null)?.from;
  return from && from.startsWith('/') && !from.startsWith('//') ? from : '/';
}
