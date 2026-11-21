import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

/** A modal on the native <dialog>: focus trap, Escape and backdrop for free. */
export function Dialog({ open, onClose, title, children, wide }: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className={`m-auto w-[calc(100%-2rem)] ${wide ? 'max-w-3xl' : 'max-w-lg'} rounded-xl border border-line bg-surface-1 p-0 text-gray-100 backdrop:bg-black/60`}
    >
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-center justify-between border-b border-line-soft px-5 py-4">
            <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
            <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-2 text-gray-400 hover:bg-hover hover:text-gray-100">
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
          <div className="overflow-y-auto px-5 py-4">{children}</div>
        </div>
      )}
    </dialog>
  );
}
