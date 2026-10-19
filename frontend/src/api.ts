import { diagnosticRequestId, recordDiagnostic } from "./diagnostics";

export type TableKey = "works" | "characters" | "cliches";
export type Item = {
  id: string;
  values: Record<string, string>;
  revision: number;
};
export type TableMeta = {
  key: TableKey;
  label: string;
  id_field: string;
  columns: string[];
  count: number;
};
export type Stats = {
  works: number;
  characters: number;
  cliches: number;
  work_cliches: number;
};
export type MaterialRelationship = {
  id: string;
  type: string;
  label: string;
  description: string;
  direction: "outgoing" | "incoming";
};
export type RelatedItem = Item & {
  relationship?: MaterialRelationship;
  relationships?: MaterialRelationship[];
};
export type Relations = Record<TableKey, RelatedItem[]>;
export type Page = {
  items: Item[];
  total: number;
  offset: number;
  limit: number;
};
export type Preview = {
  batch_id: string;
  tables: {
    table: string;
    filename: string;
    rows: number;
    new: number;
    existing: number;
  }[];
  errors: string[];
  warnings: string[];
  stats: Stats;
  format?: "db" | "csv" | "xlsx";
  schema_version?: number;
  bundle_id?: string | null;
  legacy?: boolean;
  required_tables?: {
    table: string;
    label: string;
    filename: string;
    present: boolean;
  }[];
  guidance?: string[];
};
export type LibraryFormats = {
  default: "db" | "csv" | "xlsx";
  schema_version: number;
  tables: { table: string; label: string; filename: string; columns: string[] }[];
  formats: { format: "db" | "csv" | "xlsx"; label: string; extension: string; description: string }[];
  legacy_csv: { table: string; filename: string }[];
  guidance: string[];
};
export class ApiError extends Error {
  status: number;
  diagnosticId?: string;
  requestId?: string;
  constructor(message: string, status: number, diagnosticId?: string, requestId?: string) {
    super(diagnosticId ? `${message}（诊断 ${diagnosticId}）` : message);
    this.name = "ApiError";
    this.status = status;
    this.diagnosticId = diagnosticId;
    this.requestId = requestId;
  }
}
export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const requestId = diagnosticRequestId();
  const started = Date.now();
  const headers = new Headers(init?.headers);
  headers.set("X-Request-ID", requestId);
  let response: Response;
  try {
    response = await fetch(`/api${url}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    recordDiagnostic("network", `${init?.method || "GET"} connection failed`, url, { request_id: requestId, duration_ms: Date.now() - started });
    throw new ApiError("无法连接本地服务，请确认 InkReference 已启动。", 0, undefined, requestId);
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      const detail = data.detail;
      message =
        typeof detail === "string"
          ? detail
          : detail?.message || JSON.stringify(detail || data);
    } catch {
      /* preserve status fallback */
    }
    const diagnosticId = response.headers.get("X-Diagnostic-ID") || undefined;
    const correlation = response.headers.get("X-Request-ID") || requestId;
    recordDiagnostic("http", `${init?.method || "GET"} ${response.status}`, url, { request_id: correlation, diagnostic_id: diagnosticId, duration_ms: Date.now() - started });
    throw new ApiError(message, response.status, diagnosticId, correlation);
  }
  return response.json() as Promise<T>;
}
export const jsonBody = (data: unknown) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});
export async function download(path: string, fallback: string, init?: RequestInit) {
  const requestId = diagnosticRequestId();
  const started = Date.now();
  const headers = new Headers(init?.headers);
  headers.set("X-Request-ID", requestId);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    recordDiagnostic("network", `${init?.method || "GET"} connection failed`, path, { request_id: requestId, duration_ms: Date.now() - started });
    throw new ApiError("无法连接本地服务，请确认 InkReference 已启动。", 0, undefined, requestId);
  }
  if (!response.ok) {
    const diagnosticId = response.headers.get("X-Diagnostic-ID") || undefined;
    const correlation = response.headers.get("X-Request-ID") || requestId;
    recordDiagnostic("http", `${init?.method || "GET"} ${response.status}`, path, { request_id: correlation, diagnostic_id: diagnosticId, duration_ms: Date.now() - started });
    let message = `导出失败（${response.status}）`;
    try {
      const d = await response.json();
      message = typeof d.detail === "string" ? d.detail : message;
    } catch {
      /* use fallback */
    }
    throw new ApiError(message, response.status, diagnosticId, correlation);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const filename = encoded
    ? decodeURIComponent(encoded)
    : disposition.match(/filename="([^"]+)"/)?.[1] || fallback;
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
