import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { currentUser, logout, onAuthChange, type User } from "./lib/api";
import { CoveragePage } from "./pages/Coverage";
import { ExportsPage } from "./pages/Exports";
import { FlagsPage } from "./pages/Flags";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import { ReplayPage } from "./pages/Replay";
import { TeamPage } from "./pages/Team";

const NAV = [
  ["/live", "Live map"],
  ["/replay", "Route replay"],
  ["/team", "Team overview"],
  ["/coverage", "Coverage"],
  ["/flags", "Flags"],
  ["/exports", "Exports"],
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
        </nav>
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
        <Route path="*" element={<Navigate to="/live" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
