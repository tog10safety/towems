import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import { useTheme } from "../theme/ThemeContext.jsx";
import Icon from "./Icon.jsx";
import NotificationBell from "./NotificationBell.jsx";
import OnlineNow from "./OnlineNow.jsx";
import { Badge, Button } from "./ui.jsx";

const NAV_GROUPS = [
  {
    label: "Operations",
    items: [
      { to: "/", label: "Dashboard", end: true, icon: "dashboard" },
      // Ground and air operations are separate registers with their own pages.
      // Planning itself stays the New mission form, which the server gates on
      // ``manage_missions``.
      {
        to: "/missions/ground",
        label: "Ground Missions",
        icon: "missions",
        permission: "can_view_missions",
      },
      {
        to: "/missions/air",
        label: "Air Missions",
        icon: "missions",
        permission: "can_view_missions",
      },
      { to: "/risk", label: "Risk Management", icon: "risk" },
      // The hazard library is the Safety Officer's to maintain; command,
      // operations posts, Creators and Evaluators read it, and every other
      // account gets no menu entry and no API access.
      {
        to: "/hazards",
        label: "Hazard Registry",
        icon: "shield",
        permission: "can_view_hazards",
      },
      // Anyone can be tagged for post-mission feedback, so the page is open; the
      // Safety Officer's audit queue is a tab inside it.
      { to: "/feedback", label: "Post-Mission Feedback", icon: "feedback" },
      {
        to: "/safety-audit",
        label: "Safety Audit",
        icon: "audit",
        permission: "can_view_safety_audit",
      },
      // The Operations Center keeps its working picture here; the server
      // enforces the same capability on every write.
      {
        to: "/operational-mapping",
        label: "Operational Mapping",
        icon: "map",
        permission: "maintain_aor",
      },
      // Administrators manage AOR coverage here; command, operations and safety
      // posts get the map-only viewer. The server decides the flag.
      { to: "/aor", label: "AOR / GIS", icon: "map", permission: "can_view_aor" },
    ],
  },
  {
    label: "Resources",
    items: [
      // The whole group is staff/command work; the Creator account receives
      // instructions instead and keeps none of these registers.
      {
        to: "/personnel",
        label: "Personnel",
        icon: "personnel",
        capability: "view_resources",
      },
      // Unit management is Administrator-only; the server refuses writes from
      // every other role, and this flag keeps the item off their menu.
      { to: "/units", label: "Units", icon: "units", capability: "system_config" },
      {
        to: "/offices",
        label: "Offices & Sections",
        icon: "offices",
        capability: "view_resources",
      },
      {
        to: "/inventory",
        label: "Inventory",
        icon: "inventory",
        capability: "view_resources",
      },
      {
        to: "/air-assets",
        label: "Air Assets",
        icon: "aircraft",
        capability: "view_resources",
      },
      {
        to: "/mobility-assets",
        label: "Mobility Assets",
        icon: "vehicles",
        capability: "view_resources",
      },
    ],
  },
  {
    label: "Command",
    items: [
      { to: "/instructions", label: "Instructions", icon: "instructions" },
      { to: "/reports", label: "Reports", icon: "reports" },
      { to: "/audit", label: "Audit Trail", icon: "audit", permission: "view_audit_log" },
      { to: "/admin/users", label: "User Management", icon: "users", permission: "manage_users" },
    ],
  },
];

export default function Layout() {
  const { user, logout, permissions } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    setMenuOpen(false);
  }, [navigate]);

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  const groups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) =>
        (!item.permission || permissions[item.permission]) &&
        (!item.capability || permissions.capabilities?.[item.capability])
    ),
  })).filter((group) => group.items.length);

  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`} aria-label="Main navigation">
        <div className="brand">
          <span className="brand-mark">
            TOWEMS
            <span className="brand-status" aria-hidden="true" />
          </span>
          <span className="brand-sub">Tactical Operations Wings</span>
        </div>
        <nav>
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}
                >
                  <Icon name={item.icon} className="nav-icon" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-chip">
            {user?.avatar_url ? (
              <img
                className="user-avatar user-avatar-photo"
                src={user.avatar_url}
                alt=""
                aria-hidden="true"
              />
            ) : (
              <span className="user-avatar" aria-hidden="true">
                {(user?.full_name || user?.username || "?").trim().slice(0, 1).toUpperCase()}
              </span>
            )}
            <div className="user-chip-text">
              <span className="user-name">{user?.full_name || user?.username}</span>
              <span className="user-handle">@{user?.username}</span>
              {/* Every role in force - account role plus units posts - so the
                  shell reflects what the server will actually allow. */}
              <Badge tone={permissions.is_system_owner ? "danger" : "info"}>
                {permissions.role_labels?.length
                  ? permissions.role_labels.slice(0, 2).join(" · ") +
                    (permissions.role_labels.length > 2
                      ? ` +${permissions.role_labels.length - 2}`
                      : "")
                  : user?.authority}
              </Badge>
            </div>
          </div>
          <div className="btn-row">
            <NavLink className="btn btn-ghost btn-sm" to="/profile" onClick={() => setMenuOpen(false)}>
              My profile
            </NavLink>
            <Button variant="ghost" size="sm" onClick={handleLogout}>
              Sign out
            </Button>
          </div>
        </div>
      </aside>

      {menuOpen && <div className="sidebar-scrim" onClick={() => setMenuOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button
            className="icon-btn menu-toggle"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label="Toggle navigation"
            aria-expanded={menuOpen}
          >
            <Icon name={menuOpen ? "close" : "menu"} size={20} />
          </button>
          <div className="topbar-meta">
            <span className="unit-label">
              {user?.unit_acronym || user?.unit_name || "Unassigned unit"}
            </span>
            {user?.password_change_required && <Badge tone="warning">Password change due</Badge>}
            <button
              className="icon-btn"
              onClick={toggleTheme}
              aria-label={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} appearance`}
              title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} appearance`}
            >
              <Icon name={resolvedTheme === "dark" ? "sun" : "moon"} size={19} />
            </button>
            {/* Who is on the system, in the top bar beside Settings. */}
            <OnlineNow />
            <NavLink
              className="icon-btn"
              to="/settings"
              aria-label="Settings"
              title="Settings"
            >
              <Icon name="settings" size={19} />
            </NavLink>
            <NotificationBell />
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
