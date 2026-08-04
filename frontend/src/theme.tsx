import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";

export type Theme = "dark" | "light";

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  toggle: () => {},
  setTheme: () => {},
});

const STORAGE_KEY = "fiq-theme";

// Resolve the first-paint theme: an explicit saved choice wins, otherwise
// follow the OS preference, defaulting to dark. Mirrors the inline script in
// index.html (which sets data-theme before React mounts to avoid a flash).
export function resolveInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyThemeAttribute(t: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", t);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(resolveInitialTheme);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  useEffect(() => {
    // Safety re-apply (covers the first mount, where index.html's inline
    // script already set the attribute) plus persistence.
    applyThemeAttribute(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // storage unavailable — theme still applies for this session
    }
  }, [theme]);

  // The DOM attribute is set BEFORE the state update, not in an effect after
  // it. Charts read their palette from CSS custom properties during render;
  // if the attribute only landed in a post-render effect they would build
  // their options from the outgoing theme's colours and never re-render —
  // black bars on a black canvas after a toggle to dark.
  const setTheme = useCallback((t: Theme) => {
    applyThemeAttribute(t);
    setThemeState(t);
  }, []);

  const toggle = useCallback(
    () => setTheme(themeRef.current === "dark" ? "light" : "dark"),
    [setTheme]
  );

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
