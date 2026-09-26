import { DashboardPage } from "./pages/DashboardPage";
import { DashboardThemeProvider } from "./theme/DashboardTheme";

export default function App() {
  return (
    <DashboardThemeProvider>
      <DashboardPage />
    </DashboardThemeProvider>
  );
}
