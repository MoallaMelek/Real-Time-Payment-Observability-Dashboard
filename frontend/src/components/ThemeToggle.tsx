import { useDashboardTheme } from "../theme/DashboardTheme";

export function ThemeToggle() {
  const { theme, toggleTheme, language, setLanguage, t } = useDashboardTheme();
  const isLight = theme === "light";

  return (
    <div className="preference-controls" aria-label="Display preferences">
      <button type="button" onClick={toggleTheme} className="preference-button" aria-label={`Switch to ${isLight ? "dark" : "light"} mode`}>
        <span aria-hidden="true">{isLight ? "🌙" : "☀️"}</span>
        {isLight ? t("dark") : t("light")}
      </button>
      <div className="segmented-control" aria-label="Language">
        <button type="button" className={language === "fr" ? "is-selected" : ""} onClick={() => setLanguage("fr")} aria-pressed={language === "fr"}>
          🇫🇷 FR
        </button>
        <button type="button" className={language === "en" ? "is-selected" : ""} onClick={() => setLanguage("en")} aria-pressed={language === "en"}>
          🇬🇧 EN
        </button>
      </div>
    </div>
  );
}
