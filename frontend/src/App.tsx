import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { TransitionProvider } from "./context/TransitionContext";
import Layout from "./components/Layout";
import TruckTransition from "./components/TruckTransition";
import Landing from "./pages/Landing";
import AuthPage from "./pages/AuthPage";
import PortalWorkspace from "./pages/PortalWorkspace";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <TransitionProvider>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Landing />} />
              <Route path="/auth/:serviceId" element={<AuthPage />} />
              <Route path="/portal/:serviceId" element={<PortalWorkspace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
          <TruckTransition />
        </TransitionProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
