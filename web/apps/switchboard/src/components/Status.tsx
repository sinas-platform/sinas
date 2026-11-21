import type { ReactNode } from 'react';

type Tone = 'green' | 'amber' | 'red' | 'gray' | 'blue';

const TONES: Record<Tone, string> = {
  green: 'bg-green-500/15 text-green-400',
  amber: 'bg-amber-500/15 text-amber-400',
  red: 'bg-red-500/15 text-red-400',
  gray: 'bg-hover-strong text-gray-300',
  blue: 'bg-blue-500/15 text-blue-300',
};

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ${TONES[tone]}`}>{children}</span>;
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center whitespace-nowrap rounded-md bg-hover-strong px-2 py-0.5 text-xs text-gray-300">{children}</span>;
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div className="flex max-w-2xl flex-col gap-1.5">
        <h1 className="text-[28px] font-bold leading-tight text-gray-100">{title}</h1>
        {description && <p className="text-[15px] leading-relaxed text-gray-400">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2.5">{actions}</div>}
    </header>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <p className="font-medium text-gray-200">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-sm text-gray-400">{children}</div>}
    </div>
  );
}
