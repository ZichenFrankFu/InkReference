import { jsonBody, request } from "./api";
export const studio = <T>(path: string, init?: RequestInit) =>
  request<T>(`/studio${path}`, init);
export const send = <T>(path: string, data: unknown, method = "POST") =>
  studio<T>(path, { method, ...jsonBody(data) });
export type ModelProfile = {
  id: string;
  name: string;
  provider: "openai_compatible" | "ollama";
  connection_mode: "local" | "remote";
  credential_mode: "session" | "vault" | "environment";
  base_url: string;
  model: string;
  context_window: number;
  max_output_tokens: number;
  timeout_seconds: number;
  key_env?: string;
  api_key?: string;
  has_session_key?: boolean;
  has_vault_key?: boolean;
  key_available?: boolean;
  credential_status?: "available" | "missing" | "vault_unavailable";
  target_fingerprint?: string;
  max_tokens_parameter?: "max_tokens" | "max_completion_tokens";
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  currency: string;
};
export type Skill = {
  id: string;
  version: string;
  name: string;
  description: string;
  prompt: string;
  output_schema: Record<string, unknown>;
  builtin?: boolean;
  contract_version?: number;
  stage?: "plot" | "characters" | "settings" | "language" | "all";
};
export type Workflow = {
  id: string;
  version: string;
  name: string;
  skill_id: string;
  skill_version: string;
  nodes: Record<string, boolean>;
  custom_prompt: string;
  chunk_chars: number;
  retry_limit: number;
  max_input_tokens: number;
  max_output_tokens: number;
  budget_tokens: number;
  builtin?: boolean;
  fixed_pipeline?: boolean;
  reuse_candidates?: boolean;
  stage_prompts?: Partial<Record<"plot" | "characters" | "settings" | "language", string>>;
  stage_skills?: Partial<Record<"plot" | "characters" | "settings" | "language", { id: string; version: string }>>;
  stage_models?: Partial<Record<"plot" | "characters" | "settings" | "language", string>>;
  stage_settings?: Partial<Record<"plot" | "characters" | "settings" | "language", { max_output_tokens?: number; budget_tokens?: number }>>;
};
export type DocumentMeta = {
  id: string;
  title: string;
  filename: string;
  format: string;
  encoding: string;
  latest_version_id: string;
  created_at: string;
  source_characters: number;
  current_characters?: number;
  work_id?: string;
  work_category?: string;
  catalog_author?: string;
  book_title?: string;
  author?: string;
  earliest_update_at?: string | null;
  book_type?: string;
  metadata_revision?: number;
  storage_folder?: string;
  storage_state?: string;
  versions?: { id: string; version_number: number; created_at: string }[];
};
export type Segment = {
  id?: string;
  title?: string;
  number?: number;
  start?: number;
  end?: number;
  start_offset?: number;
  end_offset?: number;
  text?: string;
};
export type TextVersion = {
  characters?: number;
  id: string;
  document_id: string;
  version_number: number;
  text: string;
  source_text: string;
  chapters: Segment[];
  chunks: Segment[];
  diagnostics: unknown;
  changes: unknown;
  diff: string;
  diff_truncated?: boolean;
  coverage: Record<string, unknown>;
  options: Record<string, unknown>;
  created_at: string;
};
export type EvidenceItem = {
  name?: string;
  description?: string;
  evidence?: string;
  quote?: string;
  start?: number;
  end?: number;
  chunk_id?: string;
  text?: string;
  [key: string]: unknown;
};
export type Run = {
  id: string;
  document_id: string;
  version_id: string;
  workflow_id: string;
  workflow_version: string;
  profile_id: string;
  status: string;
  stage: string;
  current_stage?: string;
  stages?: { stage: string; completed: number; total: number; status: string }[];
  completed: number;
  total: number;
  progress: number;
  events: { time: string; stage: string; message: string; chunk_id?: string }[];
  results: {
    summary: string;
    characters: EvidenceItem[];
    events: EvidenceItem[];
    cliches: EvidenceItem[];
  };
  candidates: {
    chunk_id: string;
    status: string;
    attempts: number;
    error?: string;
    result?: unknown;
  }[];
  coverage: {
    total_characters: number;
    covered_characters: number;
    ratio: number;
    complete: boolean;
  };
  revision: number;
  error?: string;
  summary_sources?: { chunk_id: string; start: number; end: number }[];
  snapshots?: unknown;
  created_at?: string;
};
export type List<T> = { items: T[] };
export function describe(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
export function normalizeList<T>(v: List<T> | T[]): T[] {
  return Array.isArray(v) ? v : v?.items || [];
}
