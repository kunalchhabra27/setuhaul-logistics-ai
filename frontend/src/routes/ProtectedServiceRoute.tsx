import type { ReactNode } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getService, type ServiceId } from "../data/services";

export default function ProtectedServiceRoute({ children }: { children: ReactNode }) {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const location = useLocation();
  const { loading, session, canAccess } = useAuth();

  if (!service) return <Navigate to="/" replace />;
  if (loading) return null;
  if (!session) return <Navigate to={`/auth/${service.id}`} replace state={{ from: location.pathname }} />;
  if (!canAccess(service.id as ServiceId)) return <Navigate to={`/auth/${service.id}?denied=1`} replace />;
  return <>{children}</>;
}
