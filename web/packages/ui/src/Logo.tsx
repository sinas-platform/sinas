/** The Sinas mark. */
export function SinasMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="10 15 80 70" fill="none" aria-hidden="true">
      <g transform="rotate(45 50 50) translate(4 -16)">
        <path d="M 50 25 A 25 25 0 0 1 50 75 L 50 50 Z" fill="#ea580c" />
      </g>
      <g transform="rotate(45 50 50)">
        <path d="M 50 25 A 25 25 0 0 0 50 75 L 50 50 Z" fill="#ea580c" />
      </g>
    </svg>
  );
}
