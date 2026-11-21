import { useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { getTheme, isLight, setTheme } from './theme';

export function ThemeToggle() {
  const [light, setLight] = useState(() => isLight(getTheme()));
  const toggle = () => {
    setTheme(light ? 'dark' : 'light');
    setLight(!light);
  };
  const label = light ? 'Switch to dark mode' : 'Switch to light mode';
  return (
    <button type="button" onClick={toggle} title={label} aria-label={label} className="p-2 text-gray-400 hover:text-gray-200 hover:bg-hover rounded-md">
      {light ? <Moon className="w-5 h-5" aria-hidden="true" /> : <Sun className="w-5 h-5" aria-hidden="true" />}
    </button>
  );
}
