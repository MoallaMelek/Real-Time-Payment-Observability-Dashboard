import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { translations, type DashboardLanguage, type TranslationKey } from "../i18n/dashboard";

export type DashboardTheme = "dark" | "light";

interface DashboardThemeContextValue {
  theme: DashboardTheme;
  language: DashboardLanguage;
  t: (key: TranslationKey) => string;
  toggleTheme: () => void;
  setLanguage: (language: DashboardLanguage) => void;
}

const THEME_STORAGE_KEY = "dashboard-theme";
const LANGUAGE_STORAGE_KEY = "dashboard-language";
const DashboardThemeContext = createContext<DashboardThemeContextValue | null>(null);

function initialTheme(): DashboardTheme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function initialLanguage(): DashboardLanguage {
  if (typeof window === "undefined") return "fr";
  const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (stored === "fr" || stored === "en") return stored;
  return window.navigator.language.toLowerCase().startsWith("fr") ? "fr" : "en";
}

export function DashboardThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<DashboardTheme>(initialTheme);
  const [language, setLanguageState] = useState<DashboardLanguage>(initialLanguage);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.lang = language;
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }, [language]);

  const value = useMemo<DashboardThemeContextValue>(
    () => ({
      theme,
      language,
      t: (key) => translations[language][key],
      toggleTheme: () => setTheme((current) => (current === "dark" ? "light" : "dark")),
      setLanguage: (nextLanguage) => setLanguageState(nextLanguage),
    }),
    [theme, language],
  );

  return <DashboardThemeContext.Provider value={value}>{children}</DashboardThemeContext.Provider>;
}

export function useDashboardTheme(): DashboardThemeContextValue {
  const value = useContext(DashboardThemeContext);
  if (!value) {
    throw new Error("useDashboardTheme must be used inside DashboardThemeProvider.");
  }
  return value;
}
