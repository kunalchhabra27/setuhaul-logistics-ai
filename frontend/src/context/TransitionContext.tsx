import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { type ServiceId, getService } from "../data/services";

interface TransitionState {
  active: boolean;
  service: ServiceId | null;
}

interface TransitionContextValue extends TransitionState {
  goToService: (id: ServiceId) => void;
}

const TransitionContext = createContext<TransitionContextValue | null>(null);

const RIDE_MS = 1450;

export function TransitionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<TransitionState>({ active: false, service: null });
  const { isAuthed } = useAuth();
  const navigate = useNavigate();

  const isAuthedRef = useRef(isAuthed);
  useEffect(() => {
    isAuthedRef.current = isAuthed;
  }, [isAuthed]);

  const activeRef = useRef(state.active);
  useEffect(() => {
    activeRef.current = state.active;
  }, [state.active]);

  const goToService = useCallback(
    (id: ServiceId) => {
      if (activeRef.current) return;
      setState({ active: true, service: id });
      window.setTimeout(() => {
        const destination = isAuthedRef.current(id) ? `/portal/${id}` : `/auth/${id}`;
        navigate(destination);
        setState({ active: false, service: null });
      }, RIDE_MS);
    },
    [navigate]
  );

  const value = useMemo(
    () => ({ ...state, goToService }),
    [state, goToService]
  );

  return <TransitionContext.Provider value={value}>{children}</TransitionContext.Provider>;
}

export function useTransition() {
  const ctx = useContext(TransitionContext);
  if (!ctx) throw new Error("useTransition must be used within TransitionProvider");
  return ctx;
}

export function activeServiceDef(id: ServiceId | null) {
  return id ? getService(id) : undefined;
}
