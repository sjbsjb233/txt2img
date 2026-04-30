import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../store/auth.js";

// Admin-only wrapper. Sits *inside* RequireAuth (which already redirects
// unauthenticated users to /login), so by the time we get here we know
// there's a cached user. The actual security boundary is the backend's
// `Depends(get_current_admin)` — this component just keeps non-admins
// from rendering an admin shell that would 403 on every request.
//
// Impersonate tokens carry role=user (the target's role), so when an
// admin impersonates a normal user this guard correctly bounces them
// out of /admin until they switch back. That's by design — the
// impersonating admin should not be able to flip between admin and
// "as user" views with the same tab; they open a fresh window.
export default function RequireAdmin({ children }) {
  const location = useLocation();
  const { isAdmin, isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }
  return children;
}
