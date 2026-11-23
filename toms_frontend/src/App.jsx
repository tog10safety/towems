import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import ProtectedRoute from "./components/ProtectedRoute.jsx";
import { Loading } from "./components/ui.jsx";

// Keep the shell eager and load each page on first visit. The operational app
// has many heavy screens (maps, PDF viewer, reports), and bundling them all into
// the login chunk delayed first paint for everyone.
const AORPage = lazy(() => import("./pages/AORPage.jsx"));
const AirAssetsPage = lazy(() => import("./pages/AirAssetsPage.jsx"));
const AuditPage = lazy(() => import("./pages/AuditPage.jsx"));
const DashboardPage = lazy(() => import("./pages/DashboardPage.jsx"));
const DriverTrackingPage = lazy(() => import("./pages/DriverTrackingPage.jsx"));
const FeedbackPage = lazy(() => import("./pages/FeedbackPage.jsx"));
const HazardRegistryPage = lazy(() => import("./pages/HazardRegistryPage.jsx"));
const InstructionDetailPage = lazy(() =>
  import("./pages/InstructionDetailPage.jsx")
);
const InstructionsPage = lazy(() => import("./pages/InstructionsPage.jsx"));
const InventoryPage = lazy(() => import("./pages/InventoryPage.jsx"));
const LoginPage = lazy(() => import("./pages/LoginPage.jsx"));
const MfaSetupPage = lazy(() => import("./pages/MfaSetupPage.jsx"));
const MissionDetailPage = lazy(() => import("./pages/MissionDetailPage.jsx"));
const MissionFormPage = lazy(() => import("./pages/MissionFormPage.jsx"));
const MissionsPage = lazy(() => import("./pages/MissionsPage.jsx"));
const MissionTrackPage = lazy(() => import("./pages/MissionTrackPage.jsx"));
const MobilityAssetsPage = lazy(() => import("./pages/MobilityAssetsPage.jsx"));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage.jsx"));
const OfficeSectionsPage = lazy(() => import("./pages/OfficeSectionsPage.jsx"));
const OperationalMappingPage = lazy(() =>
  import("./pages/OperationalMappingPage.jsx")
);
const PersonnelPage = lazy(() => import("./pages/PersonnelPage.jsx"));
const ProfilePage = lazy(() => import("./pages/ProfilePage.jsx"));
const ReportsPage = lazy(() => import("./pages/ReportsPage.jsx"));
const ResetPasswordPage = lazy(() => import("./pages/ResetPasswordPage.jsx"));
const MrmEditPage = lazy(() => import("./pages/MrmEditPage.jsx"));
const MrmViewerPage = lazy(() => import("./pages/MrmViewerPage.jsx"));
const RiskDetailPage = lazy(() => import("./pages/RiskDetailPage.jsx"));
const RiskPage = lazy(() => import("./pages/RiskPage.jsx"));
const SafetyAuditPage = lazy(() => import("./pages/SafetyAuditPage.jsx"));
const SettingsPage = lazy(() => import("./pages/SettingsPage.jsx"));
const UnitChangeRequestsPage = lazy(() =>
  import("./pages/UnitChangeRequestsPage.jsx")
);
const UnitsPage = lazy(() => import("./pages/UnitsPage.jsx"));
const UserManagementPage = lazy(() =>
  import("./pages/UserManagementPage.jsx")
);
const VehiclesPage = lazy(() => import("./pages/VehiclesPage.jsx"));

export default function App() {
  return (
    <Suspense
      fallback={
        <div className="splash">
          <Loading label="Loading page…" />
        </div>
      }
    >
      <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route
        path="/mfa-setup"
        element={
          <ProtectedRoute>
            <MfaSetupPage />
          </ProtectedRoute>
        }
      />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<DashboardPage />} />
        {/* Ground and air are separate registers with their own pages; the bare
            path keeps old bookmarks working by opening the ground list. */}
        <Route path="missions" element={<Navigate to="/missions/ground" replace />} />
        <Route path="missions/ground" element={<MissionsPage category="ground" />} />
        <Route path="missions/air" element={<MissionsPage category="air" />} />
        <Route path="missions/new" element={<MissionFormPage />} />
        <Route path="missions/:id/edit" element={<MissionFormPage />} />
        <Route path="missions/:id" element={<MissionDetailPage />} />
        <Route path="missions/:id/track" element={<MissionTrackPage />} />
        <Route path="missions/:id/track/driver" element={<DriverTrackingPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="risk/:id/edit" element={<MrmEditPage />} />
        <Route path="risk/:id/view" element={<MrmViewerPage />} />
        <Route path="risk/:id" element={<RiskDetailPage />} />
        <Route path="hazards" element={<HazardRegistryPage />} />
        <Route path="feedback" element={<FeedbackPage />} />
        <Route
          path="safety-audit"
          element={
            <ProtectedRoute safetyOnly>
              <SafetyAuditPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="personnel"
          element={
            <ProtectedRoute resourcesOnly>
              <PersonnelPage />
            </ProtectedRoute>
          }
        />
        <Route path="units" element={<UnitsPage />} />
        <Route
          path="inventory"
          element={
            <ProtectedRoute resourcesOnly>
              <InventoryPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="air-assets"
          element={
            <ProtectedRoute resourcesOnly>
              <AirAssetsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="mobility-assets"
          element={
            <ProtectedRoute resourcesOnly>
              <MobilityAssetsPage />
            </ProtectedRoute>
          }
        />
        {/* Old bookmarks keep working after the register moved out of Inventory. */}
        <Route path="vehicles" element={<Navigate to="/mobility-assets" replace />} />
        <Route
          path="trips"
          element={<Navigate to="/mobility-assets?tab=trips" replace />}
        />
        <Route path="operational-map" element={<Navigate to="/operational-mapping" replace />} />
        <Route path="operational-mapping" element={<OperationalMappingPage />} />
        <Route
          path="aor"
          element={
            <ProtectedRoute aorViewerOnly>
              <AORPage />
            </ProtectedRoute>
          }
        />
        {/* Open to everyone: a recipient must be able to read and action what
            was addressed to them. The "All visible" tab inside the page is the
            part reserved for command and the Operations Center. */}
        <Route path="instructions" element={<InstructionsPage />} />
        <Route path="instructions/:id" element={<InstructionDetailPage />} />
        <Route
          path="offices"
          element={
            <ProtectedRoute resourcesOnly>
              <OfficeSectionsPage />
            </ProtectedRoute>
          }
        />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="admin/users" element={<UserManagementPage />} />
        <Route
          path="admin/unit-changes"
          element={<UnitChangeRequestsPage />}
        />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="audit" element={<AuditPage />} />
      </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
