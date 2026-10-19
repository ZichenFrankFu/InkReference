import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode } from "react";

type Activity = { label: string; completed?: number; total?: number };
type Registry = {
  active: boolean;
  blocked: boolean;
  activity?: Activity;
  register: (id: string, value: Activity | null) => void;
  block: (id: string, value: boolean) => void;
};
const ActivityContext = createContext<Registry | null>(null);

/** Each kept-alive page and modal owns one title rail. */
export function PageActivityScope({ children, active = true, modal = false }: { children: ReactNode; active?: boolean; modal?: boolean }) {
  const parent = useContext(ActivityContext);
  const id = useId();
  const [items, setItems] = useState<Map<string, Activity>>(() => new Map());
  const [blockers, setBlockers] = useState<Set<string>>(() => new Set());
  const register = useCallback((key: string, value: Activity | null) => {
    setItems(previous => {
      const old = previous.get(key);
      if ((!old && !value) || (old && value && old.label === value.label && old.completed === value.completed && old.total === value.total)) return previous;
      const next = new Map(previous);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    });
  }, []);
  const block = useCallback((key: string, value: boolean) => {
    setBlockers(previous => {
      if (previous.has(key) === value) return previous;
      const next = new Set(previous);
      if (value) next.add(key); else next.delete(key);
      return next;
    });
  }, []);
  const parentBlock = parent?.block;
  useEffect(() => {
    if (!modal || !parentBlock) return;
    parentBlock(id, true);
    return () => parentBlock(id, false);
  }, [id, modal, parentBlock]);
  const available = active && (parent?.active ?? true);
  const value = useMemo<Registry>(() => ({ active: available, blocked: blockers.size > 0, activity: [...items.values()].at(-1), register, block }), [available, blockers, items, register, block]);
  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>;
}

export function useActivity(label: string, enabled = true, delay = 160) {
  const registry = useContext(ActivityContext);
  const id = useId();
  const [visible, setVisible] = useState(enabled && delay === 0);
  useEffect(() => {
    if (!enabled) { setVisible(false); return; }
    if (delay === 0) { setVisible(true); return; }
    setVisible(false);
    const timer = window.setTimeout(() => setVisible(true), delay);
    return () => window.clearTimeout(timer);
  }, [delay, enabled]);
  const register = registry?.register;
  useEffect(() => {
    if (!register || !enabled || !visible) return;
    register(id, { label });
    return () => register(id, null);
  }, [enabled, id, label, register, visible]);
  return { scoped: !!registry, visible };
}

export function ActivityRail({ active, label, completed, total, className = "" }: { active?: boolean; label?: string; completed?: number; total?: number; className?: string }) {
  const registry = useContext(ActivityContext);
  const current = registry?.activity;
  const visible = active ?? !!(registry?.active && !registry.blocked && current);
  const text = label || current?.label || "正在处理";
  const value = completed ?? current?.completed;
  const maximum = total ?? current?.total;
  const ratio = maximum && value !== undefined ? Math.min(100, Math.max(0, value / maximum * 100)) : undefined;
  return <div className={`activity-rail ${visible ? "active" : ""} ${ratio !== undefined ? "measured" : ""} ${className}`} role={visible ? "progressbar" : undefined} aria-label={visible ? text : undefined} aria-hidden={!visible} aria-valuemin={visible && ratio !== undefined ? 0 : undefined} aria-valuemax={visible && ratio !== undefined ? maximum : undefined} aria-valuenow={visible && ratio !== undefined ? value : undefined}>
    <i style={ratio !== undefined ? { width: `${ratio}%` } : undefined} />
  </div>;
}

type NavigationControls = { openMenu: () => void; menuOpen?: boolean; returnAction?: ReactNode };
const NavigationContext = createContext<NavigationControls | null>(null);
export function PageNavigationScope({ children, openMenu, menuOpen, returnAction }: NavigationControls & { children: ReactNode }) {
  const value = useMemo(() => ({ openMenu, menuOpen, returnAction }), [openMenu, menuOpen, returnAction]);
  return <NavigationContext.Provider value={value}>{children}</NavigationContext.Provider>;
}
export const usePageNavigation = () => useContext(NavigationContext);
