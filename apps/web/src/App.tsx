import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { currentUser, logout, onAuthChange, type User } from "./lib/api";
import { AuditPage } from "./pages/Audit";
import { CoveragePage } from "./pages/Coverage";
import { DevicesPage } from "./pages/Devices";
import { ExportsPage } from "./pages/Exports";
import { FlagsPage } from "./pages/Flags";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import { PrivacyPage } from "./pages/Privacy";
import { ReplayPage } from "./pages/Replay";
import { TeamPage } from "./pages/Team";

const NAV = [
  ["/live", "Live map"],
  ["/replay", "Route replay"],
  ["/team", "Team overview"],
  ["/coverage", "Coverage"],
  ["/flags", "Flags"],
  ["/exports", "Exports"],
  ["/phones", "Phones"],
] as const;

export function App() {
  const [user, setUser] = useState<User | null>(currentUser());
  useEffect(() => onAuthChange(setUser), []);
  if (!user) return <LoginPage />;
  return (
    <BrowserRouter>
      <header className="topbar">
        <span className="logo">RAGOH</span>
        <nav>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>
          ))}
          {user.role !== "supervisor" && (
            <NavLink to="/audit" className={({ isActive }) => (isActive ? "active" : "")}>Audit log</NavLink>
          )}
        </nav>
        <NavLink to="/privacy" className="small" style={{ color: "#d8e8df" }}>Privacy</NavLink>
        <span className="small">{user.full_name} · {user.role}</span>
        <button onClick={() => logout()}>Sign out</button>
      </header>
      <Routes>
        <Route path="/live" element={<LivePage />} />
        <Route path="/replay" element={<ReplayPage />} />
        <Route path="/team" element={<TeamPage />} />
        <Route path="/coverage" element={<CoveragePage />} />
        <Route path="/flags" element={<FlagsPage />} />
        <Route path="/flags/:id" element={<FlagsPage />} />
        <Route path="/exports" element={<ExportsPage />} />
        <Route path="/phones" element={<DevicesPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="*" element={<Navigate to="/live" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
