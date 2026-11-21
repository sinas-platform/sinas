import { useState, type ComponentType } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { LogOut, Menu, X } from 'lucide-react';
import { useAuth } from './auth';
import { SinasMark } from './Logo';
import { ThemeToggle } from './ThemeToggle';

export interface NavItem {
  name: string;
  href: string;
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  /** Match only this exact path (for an index route). */
  end?: boolean;
}

interface ShellProps {
  product: string;
  nav: NavItem[];
  /** Links to sibling apps, e.g. the console. */
  footerLinks?: { name: string; href: string }[];
}

/** The sidebar layout every app shares: product name, navigation, the
 * signed-in user, theme and sign-out. Pages render in the <Outlet />. */
export function Shell({ product, nav, footerLinks = [] }: ShellProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const signOut = async () => {
    await logout();
    navigate('/login');
  };

  const sidebar = (
    <div className="flex h-full flex-col gap-6 px-3 py-5">
      <div className="flex items-center gap-3 px-2">
        <SinasMark />
        <div className="flex min-w-0 flex-col">
          <span className="text-[15px] font-bold text-gray-100">{product}</span>
          <span className="truncate text-xs text-gray-400">{window.location.hostname}</span>
        </div>
      </div>
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {nav.map((item) => (
          <NavLink
            key={item.href}
            to={item.href}
            end={item.end}
            onClick={() => setOpen(false)}
            className={({ isActive }) =>
              `flex min-h-11 items-center gap-2.5 rounded-lg px-3 text-sm ${
                isActive ? 'bg-orange-500/15 text-orange-300' : 'text-gray-400 hover:bg-hover hover:text-gray-100'
              }`
            }
          >
            <item.icon className="h-[18px] w-[18px]" aria-hidden={true} />
            {item.name}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2 rounded-xl border border-line-soft bg-surface-1 p-3">
        <span className="text-xs text-gray-400">Signed in as</span>
        <span className="truncate text-sm text-gray-200">{user?.email}</span>
        {footerLinks.map((l) => (
          <a key={l.href} href={l.href} className="text-sm text-orange-300 hover:text-orange-200">{l.name}</a>
        ))}
        <div className="flex items-center justify-between pt-1">
          <button type="button" onClick={signOut} className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-gray-400 hover:bg-hover hover:text-gray-100">
            <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out
          </button>
          <ThemeToggle />
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-surface-page text-gray-100">
      <header className="flex items-center gap-3 border-b border-line-soft bg-surface-0 px-4 py-3 lg:hidden">
        <button type="button" onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-md p-2 text-gray-300 hover:bg-hover">
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>
        <SinasMark size={22} />
        <span className="font-semibold">{product}</span>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} aria-hidden="true" />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line-soft bg-surface-0">
            <button type="button" onClick={() => setOpen(false)} aria-label="Close menu" className="absolute right-2 top-2 rounded-md p-2 text-gray-400 hover:bg-hover">
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
            {sidebar}
          </aside>
        </div>
      )}
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-line-soft bg-surface-0 lg:block">{sidebar}</aside>
      <main className="min-w-0 px-4 py-6 sm:px-8 lg:ml-60 lg:px-10 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}
