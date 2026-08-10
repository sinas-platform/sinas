import { useState } from "react";
import { Moon, Sun } from "lucide-react";
import { getTheme, setTheme, type Theme } from "../lib/theme";

/** Cycles dark → light → dark. (A "system" option exists in lib/theme for
 * anyone who sets it programmatically; the toggle keeps to the two explicit
 * modes to stay a one-click affordance.) */
export default function ThemeToggle() {
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const isLight =
    theme === "light" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: light)").matches);

  const toggle = () => {
    const next: Theme = isLight ? "dark" : "light";
    setTheme(next);
    setThemeState(next);
  };

  return (
    <button
      onClick={toggle}
      className="ml-1 p-2 text-gray-400 hover:text-gray-200 hover:bg-hover rounded-md"
      title={isLight ? "Switch to dark mode" : "Switch to light mode"}
    >
      {isLight ? <Moon className="w-5 h-5" /> : <Sun className="w-5 h-5" />}
    </button>
  );
}
