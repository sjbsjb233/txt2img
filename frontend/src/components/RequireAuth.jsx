import { Navigate, useLocation } from "react-router-dom";
import { getToken, getCurrentUser } from "../api/client.js";

// Redirect anything routed under this guard to /login when there's no
// cached token + user. Cheap client-side gate; the backend's bearer
// requirement is the real boundary.
export default function RequireAuth({ children }) {
  const location = useLocation();
  if (!getToken() || !getCurrentUser()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
