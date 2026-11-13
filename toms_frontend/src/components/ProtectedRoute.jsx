import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import { Loading } from "./ui.jsx";

export default function ProtectedRoute({
  children,
  adminOnly = false,
  safetyOnly = false,
  resourcesOnly = false,
  aorViewerOnly = false,
}) {
  const { user, loading, permissions } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="splash">
        <Loading label="Establishing secure session…" />
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  // Newly registered personnel finish password, picture and signature setup
  // on their profile before they reach the rest of the application.
  if (user.onboarding_required && location.pathname !== "/profile") {
    return <Navigate to="/profile?onboarding=1" replace />;
  }
  // Administrator-only modules stay off every other account even when the URL
  // is opened directly (the sidebar already hides the link).
  if (adminOnly && !permissions.is_admin) {
    return <Navigate to="/" replace />;
  }
  // AOR/GIS is the Administrator's management screen plus a map-only view for
  // command, operations and safety posts. Every other role is sent home.
  if (aorViewerOnly && !permissions.can_view_aor) {
    return <Navigate to="/" replace />;
  }
  // The safety audit is the Unit Safety Officer's own workspace, not a
  // command report; the server applies the same role gate to its endpoints.
  if (safetyOnly && !permissions.can_view_safety_audit) {
    return <Navigate to="/" replace />;
  }
  // Resources - Personnel, Offices & Sections, Inventory, Air Assets and
  // Mobility Assets (vehicles and trips) - are withheld from the Creator
  // account, which receives instructions instead.
  if (resourcesOnly && !permissions.can_view_resources) {
    return <Navigate to="/" replace />;
  }
  return children;
}
