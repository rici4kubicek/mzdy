import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { api, setUnauthorizedHandler } from "./api";
import AuditPage from "./pages/AuditPage";
import BatchPage from "./pages/BatchPage";
import DashboardPage from "./pages/DashboardPage";
import EmployeesPage from "./pages/EmployeesPage";
import LoginPage from "./pages/LoginPage";
import SettingsPage from "./pages/SettingsPage";

interface User {
  username: string;
  role: string;
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const navigate = useNavigate();

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout").catch(() => undefined);
    setUser(null);
    navigate("/");
  }, [navigate]);

  if (user === undefined) return null;
  if (user === null) return <LoginPage onLogin={setUser} />;

  return (
    <>
      <header className="topbar">
        <span className="brand">Mzdy</span>
        <nav>
          <NavLink to="/" end>
            Období
          </NavLink>
          <NavLink to="/employees">Zaměstnanci</NavLink>
          <NavLink to="/settings">Nastavení</NavLink>
          <NavLink to="/audit">Historie změn</NavLink>
        </nav>
        <span className="user">
          {user.username}
          <button className="link" onClick={logout}>
            Odhlásit
          </button>
        </span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/batches/:period" element={<BatchPage />} />
          <Route path="/employees" element={<EmployeesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
