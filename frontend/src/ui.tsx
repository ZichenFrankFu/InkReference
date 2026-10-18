import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  LoaderCircle,
  X,
} from "lucide-react";
export const messageOf = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请重试。";
export function Busy({
  label = "正在处理",
  compact = false,
  inline = false,
  delay = 160,
}: {
  label?: string;
  compact?: boolean;
  inline?: boolean;
  delay?: number;
}) {
  const [visible, setVisible] = useState(delay === 0);
  useEffect(() => {
    if (delay === 0) {
      setVisible(true);
      return;
    }
    const timer = window.setTimeout(() => setVisible(true), delay);
    return () => window.clearTimeout(timer);
  }, [delay]);
  return (
    <div
      className={`busy-state ${compact ? "compact" : ""} ${inline ? "busy-inline" : "busy-floating"} ${visible ? "busy-visible" : "busy-pending"}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span>
        <LoaderCircle className="spin" size={16} />
        {label}
      </span>
      <div className="indeterminate" role="progressbar" aria-label={label}>
        <i />
      </div>
    </div>
  );
}
export function Progress({
  completed,
  total,
  stage,
  busy = false,
}: {
  completed?: number;
  total?: number;
  stage?: string;
  busy?: boolean;
}) {
  const ratio =
    total && completed !== undefined
      ? Math.min(100, Math.max(0, (completed / total) * 100))
      : null;
  return (
    <div className="task-progress">
      <div>
        <span>{stage || "等待开始"}</span>
        <strong>{ratio === null ? "" : `${completed} / ${total}`}</strong>
      </div>
      {ratio === null ? (
        <div
          className={`indeterminate ${busy ? "" : "idle"}`}
          role="progressbar"
          aria-label={stage || "处理进度"}
        >
          <i />
        </div>
      ) : (
        <div
          className="determinate"
          role="progressbar"
          aria-label={stage || "处理进度"}
          aria-valuenow={completed}
          aria-valuemax={total}
          aria-valuemin={0}
        >
          <i style={{ width: `${ratio}%` }} />
        </div>
      )}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  busy = false,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;
  const working = useRef(busy);
  working.current = busy;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement;
    ref.current?.focus();
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !working.current) close.current();
      if (e.key !== "Tab") return;
      const els = [
        ...(ref.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]',
        ) || []),
      ];
      const first = els[0],
        last = els[els.length - 1];
      if (!first) {
        e.preventDefault();
        return;
      }
      if (
        e.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === ref.current)
      ) {
        e.preventDefault();
        last.focus();
      } else if (
        !e.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === ref.current)
      ) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      document.removeEventListener("keydown", handle);
      prev?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`modal ${wide ? "modal-wide" : ""}`}
      >
        <header className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭对话框"
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
export function Notice({
  children,
  error = false,
  success = false,
}: {
  children: ReactNode;
  error?: boolean;
  success?: boolean;
}) {
  if (!children) return null;
  return (
    <div
      className={`notice ${error ? "error" : success ? "success" : "info"}`}
      role={error ? "alert" : "status"}
    >
      {success ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: React.ComponentType<{ size?: number }>;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="studio-empty">
      <div>
        <Icon size={30} />
      </div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function PageTitle({
  kicker,
  title,
  action,
}: {
  kicker: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="studio-heading">
      <div>
        <span className="eyebrow">{kicker}</span>
        <h1>{title}</h1>
      </div>
      {action}
    </header>
  );
}
export function Status({ value }: { value: string }) {
  const names: Record<string, string> = {
    queued: "等待运行",
    running: "正在分析",
    paused: "已暂停",
    failed: "运行失败",
    cancelled: "已取消",
    needs_review: "待审核",
    approved: "已入库",
    completed: "已完成",
  };
  return (
    <span className={`status-pill status-${value}`}>
      {value === "running" && <LoaderCircle size={12} className="spin" />}
      {names[value] || value}
    </span>
  );
}
export function SelectField({
  label,
  children,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  children: ReactNode;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label className="studio-field" htmlFor={id}>
      <span>{label}</span>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {children}
      </select>
    </label>
  );
}
export function Metric({
  label,
  value,
  unit,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
    </div>
  );
}
export function Rows({ children }: { children: ReactNode }) {
  return <div className="resource-rows">{children}</div>;
}
export function ResourceRow({
  title,
  subtitle,
  active,
  onClick,
  extra,
}: {
  title: string;
  subtitle?: string;
  active?: boolean;
  onClick: () => void;
  extra?: ReactNode;
}) {
  return (
    <button
      className={`resource-row ${active ? "active" : ""}`}
      onClick={onClick}
    >
      <span>
        <strong>{title}</strong>
        {subtitle && <small>{subtitle}</small>}
      </span>
      {extra || <ChevronRight size={16} />}
    </button>
  );
}
export const dateLabel = (date?: string) =>
  date
    ? new Date(date).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
