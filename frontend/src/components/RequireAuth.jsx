import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../store/auth.js";

// Redirect anything routed under this guard to /login when there's no
// cached token + user. Cheap client-side gate; the backend's bearer
// requirement is the real boundary. Subscribed to the auth store so a
// `forceLogout` triggered by apiFetch's 401 interceptor immediately
// re-evaluates the guard.
export default function RequireAuth({ children }) {
  const location = useLocation();
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
