export type DiagnosticEvent = {
  kind: string;
  message: string;
  path?: string;
  route?: string;
  stack?: string;
  request_id?: string;
  diagnostic_id?: string;
  duration_ms?: number;
  at: string;
};

const recent: DiagnosticEvent[] = [];
const MAX_EVENTS = 40;

export function redactDiagnostic(value: string): string {
  return value
    .replace(/\b(?:Bearer\s+)[\w.+/~=-]+/gi, "Bearer [REDACTED]")
    .replace(/\b(?:sk|sess)-[\w-]{8,}/gi, "[REDACTED]")
    .replace(/(["']?(?:api[_-]?key|access[_-]?token|authorization|password|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,"';&}]+)/gi, "$1[REDACTED]")
    .replace(/(https?:\/\/)[^/\s@]+@/gi, "$1[REDACTED]@")
    .replace(/(https?:\/\/[^\s?#]+)[?#][^\s]*/gi, "$1")
    .replace(/(?:[A-Z]:\\Users\\|\/Users\/|\/home\/)[^\\/\s]+/gi, "[HOME]")
    .slice(0, 1200);
}

export function diagnosticRequestId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().replaceAll("-", "")
    : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

type EventContext = Pick<DiagnosticEvent, "request_id" | "diagnostic_id" | "duration_ms" | "stack">;
function currentRoute(): string {
  return typeof window === "undefined" ? "" : window.location.hash.replace(/^#\/?/, "").split(/[?/:]/, 1)[0].replace(/[^a-z_-]/gi, "").slice(0, 40);
}

export function diagnosticStack(error: unknown): string | undefined {
  if (!(error instanceof Error) || !error.stack) return undefined;
  // Error messages can contain input; only retain source locations from the stack.
  return error.stack.split("\n").slice(1).filter((line) => /^\s*(?:at |[^@]+@)/.test(line))
    .slice(0, 10).map((line) => redactDiagnostic(line)).join("\n").slice(0, 1800);
}

export function recordDiagnostic(kind: string, message: string, path?: string, context?: EventContext) {
  const event: DiagnosticEvent = {
    kind: kind.slice(0, 40),
    message: redactDiagnostic(message),
    at: new Date().toISOString(),
  };
  if (path) event.path = redactDiagnostic(path).split(/[?#]/, 1)[0].slice(0, 240);
  event.route = currentRoute();
  if (context?.request_id && /^[0-9a-f]{32}$/.test(context.request_id)) event.request_id = context.request_id;
  if (context?.diagnostic_id && /^err-[0-9a-f]{16}$/.test(context.diagnostic_id)) event.diagnostic_id = context.diagnostic_id;
  if (typeof context?.duration_ms === "number" && Number.isFinite(context.duration_ms)) event.duration_ms = Math.round(Math.max(0, context.duration_ms));
  if (context?.stack) event.stack = redactDiagnostic(context.stack).slice(0, 1800);
  recent.push(event);
  if (recent.length > MAX_EVENTS) recent.splice(0, recent.length - MAX_EVENTS);
}

export function diagnosticEvents(): DiagnosticEvent[] {
  return recent.map((event) => ({ ...event }));
}

export function openQuickRepair() {
  window.dispatchEvent(new CustomEvent("inkreference:debug-open"));
}

// Keep diagnostics in memory; source text, request bodies and credentials are not collected.
if (typeof window !== "undefined") {
  window.addEventListener("error", (event) => {
    const source = event.filename.split(/[?#]/, 1)[0];
    recordDiagnostic("javascript", event.message || "JavaScript error", `${source}:${event.lineno}:${event.colno}`, { stack: diagnosticStack(event.error) });
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    recordDiagnostic("promise", reason instanceof Error ? `${reason.name}: ${reason.message}` : "Unhandled promise rejection", undefined, { stack: diagnosticStack(reason) });
  });
}
