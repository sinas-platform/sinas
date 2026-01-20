interface SinasLogoProps {
  className?: string;
  size?: number;
}

export function SinasLogo({ className = '', size = 32 }: SinasLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="10 15 80 70"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Left half circle - tilted 45 degrees, positioned */}
      <g transform="rotate(45 50 50) translate(4 -16)">
        <path
          d="M 50 25 A 25 25 0 0 1 50 75 L 50 50 Z"
          fill="#ea580c"
        />
      </g>

      {/* Right half circle - tilted 45 degrees */}
      <g transform="rotate(45 50 50)">
        <path
          d="M 50 25 A 25 25 0 0 0 50 75 L 50 50 Z"
          fill="#ea580c"
        />
      </g>
    </svg>
  );
}
