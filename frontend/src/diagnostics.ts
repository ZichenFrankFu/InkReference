export type DiagnosticEvent = {
  kind: string;
  message: string;
  path?: string;
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

export function recordDiagnostic(kind: string, message: string, path?: string) {
  const event: DiagnosticEvent = {
    kind: kind.slice(0, 40),
    message: redactDiagnostic(message),
    at: new Date().toISOString(),
  };
  if (path) event.path = redactDiagnostic(path).split(/[?#]/, 1)[0].slice(0, 240);
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
    recordDiagnostic("javascript", event.message || "JavaScript error", event.filename);
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    recordDiagnostic("promise", reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "Unhandled promise rejection");
  });
}
