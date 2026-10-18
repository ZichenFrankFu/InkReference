import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  Activity, Check, CheckCircle2, ChevronDown, Cloud, Cpu, Eye, EyeOff,
  Globe, LockKeyhole, Plus, RefreshCw, Save, Server,
  ShieldCheck, SlidersHorizontal, Trash2, Wallet, Zap,
} from "lucide-react";
import { studio, send, normalizeList, type ModelProfile, type List } from "./studio";
import { isLoopbackEndpoint, isRemoteModel, modelEndpoint } from "./model-security";
import { Busy, Empty, Metric, Modal, Notice, PageTitle, SelectField, dateLabel, messageOf } from "./ui";
import "./model-settings.css";

const tokenSource = (value: string | undefined) =>
  ({ actual: "实测", estimated: "估算", unknown: "未知", mixed: "混合" })[value || "unknown"] || value;
const blank: ModelProfile = {
  id: "", name: "本地 Ollama", provider: "ollama", connection_mode: "local",
  credential_mode: "session", base_url: "http://127.0.0.1:11434", model: "",
  context_window: 32768, max_output_tokens: 3000, timeout_seconds: 180,
  key_env: "", api_key: "", input_price_per_million: null,
  output_price_per_million: null, currency: "USD", max_tokens_parameter: "max_tokens",
};
type Preset = {
  id: string; name: string; detail: string; mode: ModelProfile["connection_mode"];
  provider: ModelProfile["provider"]; url: string; keyEnv?: string;
};
const presets: Preset[] = [
  { id: "ollama", name: "Ollama", detail: "原生 API", mode: "local", provider: "ollama", url: "http://127.0.0.1:11434" },
  { id: "lmstudio", name: "LM Studio", detail: "OpenAI 兼容", mode: "local", provider: "openai_compatible", url: "http://127.0.0.1:1234/v1" },
  { id: "vllm", name: "vLLM", detail: "OpenAI 兼容", mode: "local", provider: "openai_compatible", url: "http://127.0.0.1:8000/v1" },
  { id: "openai", name: "OpenAI", detail: "Chat Completions", mode: "remote", provider: "openai_compatible", url: "https://api.openai.com/v1", keyEnv: "OPENAI_API_KEY" },
  { id: "deepseek", name: "DeepSeek", detail: "OpenAI 兼容", mode: "remote", provider: "openai_compatible", url: "https://api.deepseek.com", keyEnv: "DEEPSEEK_API_KEY" },
  { id: "qwen", name: "通义千问", detail: "百炼 · 北京", mode: "remote", provider: "openai_compatible", url: "https://dashscope.aliyuncs.com/compatible-mode/v1", keyEnv: "DASHSCOPE_API_KEY" },
  { id: "custom", name: "自定义 API", detail: "OpenAI 兼容", mode: "remote", provider: "openai_compatible", url: "https://", keyEnv: "" },
];
type Security = { vault_available: boolean; vault_backend?: string; vault_reason?: string; credential_modes: string[] };
type TestResult = { ok: boolean; model?: string; message: string; duration_ms?: number; call_id?: string };
const cleanProfile = (profile: ModelProfile): ModelProfile => ({
  ...blank, ...profile, api_key: "",
  connection_mode: isRemoteModel(profile) ? "remote" : "local",
  credential_mode: profile.credential_mode || (profile.key_env ? "environment" : "session"),
});
const profileValue = (profile: ModelProfile | null) => profile ? JSON.stringify({
  id: profile.id, name: profile.name, provider: profile.provider, connection_mode: profile.connection_mode,
  credential_mode: profile.credential_mode, base_url: profile.base_url, model: profile.model,
  context_window: profile.context_window, max_output_tokens: profile.max_output_tokens,
  timeout_seconds: profile.timeout_seconds, key_env: profile.key_env || "", api_key: profile.api_key || "",
  input_price_per_million: profile.input_price_per_million, output_price_per_million: profile.output_price_per_million,
  currency: profile.currency, max_tokens_parameter: profile.max_tokens_parameter || "max_tokens",
}) : "";
const validation = (profile: ModelProfile, modelRequired = true) => {
  if (!profile.name.trim()) return "请填写配置名称。";
  if (modelRequired && !profile.model.trim()) return "请填写模型 ID，或先读取模型列表。";
  try {
    const u = new URL(profile.base_url);
    if (!["http:", "https:"].includes(u.protocol) || u.username || u.password || u.search || u.hash) return "服务地址不能包含密钥、账号、查询参数或片段。";
    if (profile.connection_mode === "local" && !isLoopbackEndpoint(profile.base_url)) return "本地模式仅接受 localhost 或回环 IP 地址。远程服务请切换至商业 API。";
    if (profile.connection_mode === "remote" && u.protocol !== "https:") return "商业 API 必须使用 HTTPS。";
  } catch { return "请填写完整的 HTTP(S) 服务地址。"; }
  if (profile.credential_mode === "environment" && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(profile.key_env || "")) return "请填写有效的密钥环境变量名称。";
  if (!Number.isInteger(profile.context_window) || profile.context_window < 128 || profile.context_window > 10_000_000) return "上下文窗口须为 128 至 10000000 的整数。";
  if (!Number.isInteger(profile.max_output_tokens) || profile.max_output_tokens < 1 || profile.max_output_tokens >= profile.context_window) return "最大输出 Token 须为正整数，且小于上下文窗口。";
  if (!Number.isFinite(profile.timeout_seconds) || profile.timeout_seconds < 1 || profile.timeout_seconds > 3600) return "请求超时须在 1 至 3600 秒之间。";
  if ([profile.input_price_per_million, profile.output_price_per_million].some((value) => value != null && (!Number.isFinite(value) || value < 0 || value > 1_000_000_000))) return "Token 价格须在 0 至 1000000000 之间。";
  if ((profile.input_price_per_million != null || profile.output_price_per_million != null) && !/^[A-Za-z]{3}$/.test(profile.currency)) return "配置价格时请选择有效的三字母币种。";
  return "";
};

type Usage = {
  id?: string;
  run_id?: string;
  profile_id?: string;
  model?: string;
  status?: string;
  input_tokens?: number;
  output_tokens?: number;
  input_tokens_source?: string;
  output_tokens_source?: string;
  usage_source?: string;
  source?: string;
  created_at?: string;
  started_at?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    input_source?: string;
    output_source?: string;
  };
  duration_seconds?: number;
  error?: string;
  [key: string]: unknown;
};
type UsageSummary = {
  calls: number;
  successful_calls: number;
  failed_calls: number;
  pending_calls: number;
  actual_input_tokens: number;
  actual_output_tokens: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  unknown_input_calls: number;
  unknown_output_calls: number;
  costs: { currency: string; amount: number; source: string }[];
  unpriced_calls: number;
};

export default function Models() {
  const [tab, setTab] = useState("models");
  const [items, setItems] = useState<ModelProfile[]>([]);
  const [security, setSecurity] = useState<Security | null>(null);
  const [draft, setDraft] = useState<ModelProfile | null>(null);
  const [baseline, setBaseline] = useState("");
  const [isNew, setIsNew] = useState(false);
  const [usage, setUsage] = useState<Usage[]>([]);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [pending, setPending] = useState<{ run: () => void } | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [showKey, setShowKey] = useState(false);
  const fieldPrefix = useId();
  const editorRef = useRef<HTMLHeadingElement>(null);
  const dirty = !!draft && profileValue(draft) !== baseline;
  const saved = items.find((p) => p.id === draft?.id);
  const sameEndpoint = !!saved && !!draft && saved.provider === draft.provider &&
    saved.connection_mode === draft.connection_mode && saved.base_url.replace(/\/$/, "") === draft.base_url.replace(/\/$/, "");
  const canUseSaved = sameEndpoint && saved?.credential_mode === draft?.credential_mode && !!saved?.key_available &&
    (draft?.credential_mode !== "environment" || saved?.key_env === draft?.key_env);
  const notifyChanged = () => window.dispatchEvent(new Event("inkreference:models-changed"));
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [profiles, protection] = await Promise.all([
        studio<List<ModelProfile>>("/models"), studio<Security>("/models/security"),
      ]);
      setItems(normalizeList(profiles).map(cleanProfile));
      setSecurity(protection);
    } catch (e) { setError(messageOf(e)); }
    finally { setLoading(false); }
  }, []);
  const loadUsage = useCallback(async () => {
    setBusy("usage"); setError("");
    try {
      const result = await studio<{ items: Usage[]; summary: UsageSummary }>(`/usage${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`);
      setUsage(result.items); setSummary(result.summary);
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  }, [runId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const refresh = () => { void load(); };
    window.addEventListener("inkreference:models-refresh", refresh);
    return () => window.removeEventListener("inkreference:models-refresh", refresh);
  }, [load]);
  useEffect(() => { if (tab === "usage") void loadUsage(); }, [tab]);
  useEffect(() => {
    if (!dirty) return;
    const leave = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [dirty]);
  const activate = (profile: ModelProfile | null, creating = false) => {
    setDraft(profile); setBaseline(profileValue(profile)); setIsNew(creating);
    setTestResult(null); setDiscovered([]); setShowKey(false); setError(""); setMessage("");
    setTab("models");
    requestAnimationFrame(() => editorRef.current?.focus());
  };
  const guarded = (run: () => void) => { if (dirty) setPending({ run }); else run(); };
  const create = (mode: ModelProfile["connection_mode"] = "local") => guarded(() => {
    const preset = presets.find((p) => p.mode === mode)!;
    activate({ ...blank, name: mode === "local" ? "本地 Ollama" : "商业 API", connection_mode: mode,
      provider: preset.provider, base_url: preset.url }, true);
  });
  const update = <K extends keyof ModelProfile>(key: K, value: ModelProfile[K]) => {
    setDraft((d) => {
      if (!d) return d;
      const changeEndpoint = ["base_url", "provider", "connection_mode"].includes(key) && d[key] !== value;
      return { ...d, [key]: value, ...(changeEndpoint ? {
        api_key: "", key_env: "", key_available: false, has_session_key: false, has_vault_key: false,
      } : {}) };
    });
    setTestResult(null);
    if (["base_url", "provider", "connection_mode"].includes(key)) setDiscovered([]);
  };
  const applyPreset = (preset: Preset) => {
    if (!draft) return;
    setDraft({ ...draft, name: `${preset.mode === "local" ? "本地 " : ""}${preset.name}`,
      provider: preset.provider, connection_mode: preset.mode, base_url: preset.url,
      key_env: draft.credential_mode === "environment" ? preset.keyEnv || "" : "", api_key: "",
      has_session_key: false, has_vault_key: false, key_available: false });
    setDiscovered([]); setTestResult(null); setShowKey(false);
  };
  const payload = () => {
    if (!draft) throw new Error("请先添加模型配置。");
    return {
      id: draft.id || `model-${crypto.randomUUID()}`, name: draft.name.trim(),
      provider: draft.provider, connection_mode: draft.connection_mode, credential_mode: draft.credential_mode,
      base_url: draft.base_url.trim().replace(/\/$/, ""), model: draft.model.trim(),
      context_window: draft.context_window, max_output_tokens: draft.max_output_tokens,
      timeout_seconds: draft.timeout_seconds, key_env: draft.credential_mode === "environment" ? draft.key_env?.trim() || "" : "",
      input_price_per_million: draft.input_price_per_million, output_price_per_million: draft.output_price_per_million,
      currency: draft.currency.trim().toUpperCase(), max_tokens_parameter: draft.max_tokens_parameter || "max_tokens",
      ...(draft.credential_mode !== "environment" && draft.api_key ? { api_key: draft.api_key } : {}),
    };
  };
  const save = async () => {
    if (!draft) return false;
    const problem = validation(draft);
    if (problem) { setError(problem); return false; }
    if (draft.credential_mode === "vault" && !security?.vault_available) {
      setError(security?.vault_reason || "当前系统密钥库不可用，请选择其他凭据方式。"); return false;
    }
    setBusy("save"); setError(""); setMessage("");
    try {
      const result = cleanProfile(await send<ModelProfile>(isNew ? "/models" : `/models/${encodeURIComponent(draft.id)}`, payload(), isNew ? "POST" : "PUT"));
      setDraft(result); setBaseline(profileValue(result)); setIsNew(false); setShowKey(false);
      setMessage("模型配置已保存。"); notifyChanged(); await load(); return true;
    } catch (e) { setError(messageOf(e)); return false; }
    finally { setBusy(""); }
  };
  const perform = async (action: "test" | "discover") => {
    if (!draft) return;
    const problem = validation(draft, action === "test");
    if (problem) { setError(problem); return; }
    setBusy(action); setError(""); setMessage(""); setTestResult(null);
    try {
      const data = { ...payload(), ...(canUseSaved && !draft.api_key ? { use_saved_key: draft.id } : {}),
        remote_consent: isRemoteModel(draft) };
      if (action === "test") setTestResult(await send<TestResult>("/models/test-draft", data));
      else {
        const result = await send<{ models: { id: string }[] }>("/models/discover", data);
        const ids = result.models.map((model) => model.id);
        setDiscovered(ids);
        if (ids.length === 1) update("model", ids[0]);
        setMessage(ids.length ? `已读取 ${ids.length} 个模型。` : "服务返回了空模型列表，可手动填写模型 ID。");
      }
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const remove = async () => {
    if (!draft || isNew) return;
    setBusy("delete"); setError("");
    try {
      await studio(`/models/${encodeURIComponent(draft.id)}`, { method: "DELETE" });
      activate(null); setConfirmDelete(false); setMessage("模型配置已删除。"); notifyChanged(); await load();
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const clearCredentials = async () => {
    if (!draft || isNew) return;
    setBusy("clear"); setError("");
    try {
      const result = cleanProfile(await studio<ModelProfile>(`/models/${encodeURIComponent(draft.id)}/credentials`, { method: "DELETE" }));
      setDraft(result); setBaseline(profileValue(result)); setConfirmClear(false); setShowKey(false);
      setMessage("已清除已存凭据与环境变量引用。"); notifyChanged(); await load();
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const number = (n?: number) => Number(n || 0).toLocaleString();
  const activePreset = draft && presets.find((p) => p.provider === draft.provider && p.url === draft.base_url && p.mode === draft.connection_mode);
  return (
    <main className="studio-main model-settings-page">
      <PageTitle kicker="MODEL SETTINGS" title="模型设置" action={
        <button className="button primary" disabled={!!busy} onClick={() => create()}><Plus size={17} />添加模型</button>
      } />
      <div className="page-tabs" aria-label="模型设置分类">
        <button className={tab === "models" ? "active" : ""} disabled={!!busy} onClick={() => setTab("models")}><Server size={16} />连接配置<span>{items.length}</span></button>
        <button className={tab === "usage" ? "active" : ""} disabled={!!busy} onClick={() => setTab("usage")}><Activity size={16} />调用与用量</button>
      </div>
      <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
      {tab === "models" ? (
        <div className="connection-layout">
          <aside className="connection-sidebar" aria-label="已保存的模型">
            <div className="connection-sidebar-heading"><h2>我的模型 <span>{items.length}</span></h2><button className="icon-button" aria-label="刷新模型配置" disabled={!!busy || loading} onClick={() => void load()}><RefreshCw size={15} /></button></div>
            {loading ? <Busy label="正在读取模型配置" /> : !items.length ? (
              <p className="connection-sidebar-empty">还没有连接，选择右侧的模型来源开始。</p>
            ) : items.map((p) => (
              <button className={`connection-card ${!isNew && draft?.id === p.id ? "active" : ""}`} disabled={!!busy} key={p.id}
                aria-pressed={!isNew && draft?.id === p.id} onClick={() => guarded(() => activate(cleanProfile(p)))}>
                <span className={`connection-icon ${p.connection_mode}`}>
                  {p.connection_mode === "local" ? <Cpu size={21} /> : <Cloud size={21} />}
                </span>
                <span className="connection-card-copy"><strong>{p.name}</strong><span>{p.model}</span><small>{p.connection_mode === "local" ? "本地" : "商业 API"}{p.key_available ? " · 凭据就绪" : p.connection_mode === "remote" ? " · 待配置凭据" : ""}</small></span>
                {!isNew && draft?.id === p.id && <Check size={16} className="connection-selected" />}
              </button>
            ))}
            <button className="connection-add" disabled={!!busy} onClick={() => create()}><Plus size={16} />新建连接</button>
          </aside>
          <section className="surface connection-editor">
            {!draft ? (
              <div className="connection-welcome">
                <span className="connection-welcome-icon"><Zap size={29} /></span>
                <h2>连接你的模型</h2><p>选择模型来源，填写地址与模型，即可开始分析。</p>
                <div className="connection-start-options">
                  <button onClick={() => create("local")} disabled={loading}><Cpu size={26} /><strong>本地模型</strong><span>Ollama · LM Studio · vLLM</span><ChevronDown size={17} /></button>
                  <button onClick={() => create("remote")} disabled={loading}><Cloud size={26} /><strong>商业 API</strong><span>OpenAI · DeepSeek · 通义千问</span><ChevronDown size={17} /></button>
                </div>
              </div>
            ) : (
              <>
                <header className="connection-editor-heading"><div><h2 ref={editorRef} tabIndex={-1}>{isNew ? "添加模型连接" : draft.name}</h2><span>{dirty ? "有未保存的修改" : isNew ? "新连接" : "已保存"}</span></div>
                  {!isNew && <button className="icon-button danger" disabled={!!busy} aria-label="删除模型配置" onClick={() => setConfirmDelete(true)}><Trash2 size={18} /></button>}
                </header>
                <form onSubmit={(e) => { e.preventDefault(); void save(); }} autoComplete="off" noValidate>
                  <fieldset disabled={!!busy} className="connection-fields">
                    <legend className="sr-only">模型连接配置</legend>
                    <div className="connection-mode" role="group" aria-label="模型来源">
                      <button type="button" aria-pressed={draft.connection_mode === "local"} className={draft.connection_mode === "local" ? "active" : ""} onClick={() => draft.connection_mode !== "local" && applyPreset(presets[0])}><Cpu size={20} /><span><strong>本地模型</strong><small>连接本机推理服务</small></span>{draft.connection_mode === "local" && <CheckCircle2 size={18} />}</button>
                      <button type="button" aria-pressed={draft.connection_mode === "remote"} className={draft.connection_mode === "remote" ? "active" : ""} onClick={() => draft.connection_mode !== "remote" && applyPreset(presets[3])}><Cloud size={20} /><span><strong>商业 API</strong><small>通过 HTTPS 连接服务商</small></span>{draft.connection_mode === "remote" && <CheckCircle2 size={18} />}</button>
                    </div>
                    <div className="connection-presets" role="group" aria-label="服务预设">
                      {presets.filter((p) => p.mode === draft.connection_mode).map((p) => <button key={p.id} type="button" aria-pressed={activePreset?.id === p.id} className={activePreset?.id === p.id ? "active" : ""} onClick={() => applyPreset(p)}><strong>{p.name}</strong><small>{p.detail}</small></button>)}
                    </div>
                    <div className="connection-section">
                      <label className="studio-field" htmlFor={`${fieldPrefix}-name`}><span>配置名称</span><input id={`${fieldPrefix}-name`} required maxLength={200} value={draft.name} placeholder="例如：本地 Qwen" onChange={(e) => update("name", e.target.value)} /></label>
                      <label className="studio-field" htmlFor={`${fieldPrefix}-url`}><span>服务地址 <small>{draft.provider === "ollama" ? "Ollama API" : "OpenAI 兼容 · Chat Completions"}</small></span><input id={`${fieldPrefix}-url`} type="url" required value={draft.base_url} onChange={(e) => update("base_url", e.target.value)} spellCheck={false} autoCapitalize="off" placeholder={draft.connection_mode === "local" ? "http://127.0.0.1:1234/v1" : "https://api.example.com/v1"} /></label>
                      <div className="connection-destination" data-mode={draft.connection_mode}>{draft.connection_mode === "local" ? <Cpu size={15} /> : <Globe size={15} />}<span>{draft.connection_mode === "local" ? "本机服务" : "API 服务"}<strong>{modelEndpoint(draft.base_url, true)}</strong></span></div>
                      <div className="connection-model-field">
                        <label className="studio-field" htmlFor={`${fieldPrefix}-model`}><span>模型 ID</span><input id={`${fieldPrefix}-model`} required value={draft.model} onChange={(e) => update("model", e.target.value)} placeholder={draft.provider === "ollama" ? "例如 qwen3:8b" : "填写服务提供的模型 ID"} spellCheck={false} autoCapitalize="off" /></label>
                        <button className="button secondary" type="button" onClick={() => void perform("discover")}><RefreshCw size={15} />读取模型列表</button>
                      </div>
                      {!!discovered.length && <label className="studio-field connection-discovered" htmlFor={`${fieldPrefix}-available`}><span>可用模型</span><select id={`${fieldPrefix}-available`} aria-label="可用模型" value={discovered.includes(draft.model) ? draft.model : ""} onChange={(e) => update("model", e.target.value)}><option value="" disabled>选择一个模型</option>{discovered.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>}
                    </div>
                    <section className="connection-credentials" aria-labelledby={`${fieldPrefix}-credentials`}>
                      <header><h3 id={`${fieldPrefix}-credentials`}><LockKeyhole size={17} />调用凭据</h3>{canUseSaved && <span className="connection-ready"><ShieldCheck size={14} />已配置</span>}</header>
                      <div className="credential-choices" role="group" aria-label="凭据保存方式">
                        {([ ["session", "仅本次运行", "关闭服务后清除"], ["vault", "系统密钥库", "在本机安全保存"], ["environment", "环境变量", "读取已有配置"] ] as const).map(([mode, title, detail]) => <button type="button" key={mode} disabled={mode === "vault" && !security?.vault_available} aria-pressed={draft.credential_mode === mode} title={mode === "vault" && !security?.vault_available ? security?.vault_reason || "系统密钥库不可用" : undefined} className={draft.credential_mode === mode ? "active" : ""} onClick={() => { update("credential_mode", mode); update("api_key", ""); setShowKey(false); }}><strong>{title}</strong><small>{detail}</small></button>)}
                      </div>
                      {draft.credential_mode === "vault" && !security?.vault_available && <p className="connection-hint"><LockKeyhole size={13} />系统密钥库不可用，请选择其他凭据方式。</p>}
                      {draft.credential_mode === "environment" ? <label className="studio-field"><span>密钥环境变量名称</span><input value={draft.key_env || ""} onChange={(e) => update("key_env", e.target.value)} placeholder={activePreset?.keyEnv || "例如 OPENAI_API_KEY"} spellCheck={false} autoCapitalize="off" /></label> : <div className="studio-field"><label className="connection-label" htmlFor={`${fieldPrefix}-key`}>API Key <small>{draft.connection_mode === "local" ? "服务要求鉴权时填写" : "填写服务商颁发的密钥"}</small></label><span className="connection-password"><input id={`${fieldPrefix}-key`} aria-label="API Key" type={showKey ? "text" : "password"} value={draft.api_key || ""} onChange={(e) => update("api_key", e.target.value)} autoComplete="new-password" spellCheck={false} autoCapitalize="off" placeholder={canUseSaved ? "已配置；留空继续使用，输入可替换" : "输入 API Key"} /><button type="button" className="icon-button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? "隐藏 API Key" : "显示 API Key"} aria-pressed={showKey}>{showKey ? <EyeOff size={17} /> : <Eye size={17} />}</button></span></div>}
                      {!isNew && (saved?.has_session_key || saved?.has_vault_key || saved?.key_env) && <button type="button" className="connection-clear" disabled={dirty} title={dirty ? "请先保存或取消修改" : undefined} onClick={() => setConfirmClear(true)}><Trash2 size={13} />清除已存凭据</button>}
                    </section>
                    <details className="connection-details"><summary><SlidersHorizontal size={16} />高级参数<ChevronDown size={15} /></summary><div className="form-grid">
                      {([["context_window", "上下文窗口", 128, 10_000_000], ["max_output_tokens", "最大输出 Token", 1, draft.context_window - 1], ["timeout_seconds", "请求超时（秒）", 1, 3600]] as const).map(([key, label, min, max]) => <label className="studio-field" key={key}><span>{label}</span><input type="number" min={min} max={max} step={1} required value={draft[key]} onChange={(e) => update(key, Number(e.target.value))} /></label>)}
                      {draft.provider === "openai_compatible" && <SelectField label="最大输出参数" value={draft.max_tokens_parameter || "max_tokens"} onChange={(v) => update("max_tokens_parameter", v as ModelProfile["max_tokens_parameter"])}><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option></SelectField>}
                    </div></details>
                    <details className="connection-details"><summary><Wallet size={16} />费用估算<small>可选</small><ChevronDown size={15} /></summary><div className="form-grid">
                      {([["input_price_per_million", "每百万输入 Token"], ["output_price_per_million", "每百万输出 Token"]] as const).map(([key, label]) => <label className="studio-field" key={key}><span>{label}</span><input type="number" min={0} max={1_000_000_000} step="any" value={draft[key] ?? ""} placeholder="未配置" onChange={(e) => update(key, e.target.value === "" ? null : Number(e.target.value))} /></label>)}
                      <label className="studio-field"><span>币种</span><select value={draft.currency || "USD"} onChange={(e) => update("currency", e.target.value)}><option value="USD">USD · 美元</option><option value="CNY">CNY · 人民币</option>{draft.currency && !["USD", "CNY"].includes(draft.currency) && <option>{draft.currency}</option>}</select></label>
                    </div></details>
                  </fieldset>
                  <div className="connection-feedback" aria-live="polite">
                    {testResult && <Notice success={testResult.ok} error={!testResult.ok}><strong>{testResult.message || (testResult.ok ? "连接成功" : "连接失败")}</strong>{testResult.ok && <span>{testResult.model || draft.model}{testResult.duration_ms !== undefined ? ` · ${(testResult.duration_ms / 1000).toFixed(2)} 秒` : ""}</span>}</Notice>}
                    {busy && busy !== "usage" && <Busy label={{ save: "正在保存配置", test: "正在测试当前配置", discover: "正在读取服务模型列表", delete: "正在删除配置", clear: "正在清除已存凭据" }[busy] || "正在处理"} />}
                  </div>
                  <footer className="connection-footer">{dirty && <span>修改尚未保存</span>}<div>{dirty && <button className="button secondary" type="button" disabled={!!busy} onClick={() => guarded(() => activate(saved ? cleanProfile(saved) : null))}>取消修改</button>}<button className="button secondary" type="button" disabled={!!busy} onClick={() => void perform("test")}><Zap size={15} />测试连接</button><button className="button primary" type="submit" disabled={!!busy || (!dirty && !isNew)}><Save size={15} />保存配置</button></div></footer>
                </form>
              </>
            )}
          </section>
        </div>

      ) : (
        <section className="usage-section">
          <div className="metrics-grid">
            <Metric
              label="实际输入 token"
              value={number(summary?.actual_input_tokens)}
            />
            <Metric
              label="实际输出 token"
              value={number(summary?.actual_output_tokens)}
            />
            <Metric
              label="估算 token"
              value={number(
                (summary?.estimated_input_tokens || 0) +
                  (summary?.estimated_output_tokens || 0),
              )}
            />
            <Metric label="调用次数" value={number(summary?.calls)} unit="次" />
          </div>
          <div className="usage-cost-line">
            <span>估算费用</span>
            {summary?.costs?.length ? (
              summary.costs.map((c) => (
                <strong key={c.currency}>
                  {c.currency} {c.amount.toFixed(4)}
                </strong>
              ))
            ) : (
              <strong>暂无已计价调用</strong>
            )}
            <span>{summary?.unpriced_calls || 0} 次调用未配置价格</span>
          </div>
          <div className="usage-health-line">
            <span>成功 {number(summary?.successful_calls)} 次</span>
            <span>失败 {number(summary?.failed_calls)} 次</span>
            <span>等待返回 {number(summary?.pending_calls)} 次</span>
            <span>
              输入未知 {number(summary?.unknown_input_calls)} 次 · 输出未知{" "}
              {number(summary?.unknown_output_calls)} 次
            </span>
          </div>
          <div className="surface usage-table">
            <div className="surface-heading">
              <h2>调用明细</h2>
              <div className="inline-actions">
                <input
                  className="compact-input"
                  value={runId}
                  onChange={(e) => setRunId(e.target.value)}
                  placeholder="按任务 ID 筛选"
                  aria-label="筛选调用任务 ID"
                />
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={() => void loadUsage()}
                >
                  查询
                </button>
              </div>
            </div>
            {busy === "usage" ? (
              <Busy label="正在读取用量账本" />
            ) : !usage.length ? (
              <Empty
                icon={Activity}
                title="还没有模型调用"
                description="运行分析或测试连接后，用量会记录在这里。"
              />
            ) : (
              <div className="usage-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>时间 / 模型</th>
                      <th>状态</th>
                      <th>输入</th>
                      <th>输出</th>
                      <th>用量来源</th>
                      <th>任务</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.map((call, i) => (
                      <tr key={call.id || i}>
                        <td>
                          <strong>{call.model || call.profile_id}</strong>
                          <small>
                            {dateLabel(call.started_at || call.created_at)}
                          </small>
                        </td>
                        <td>
                          <span
                            className={`status-pill status-${call.status === "success" ? "approved" : call.status === "error" ? "failed" : "queued"}`}
                          >
                            {call.status === "success"
                              ? "成功"
                              : call.status === "error"
                                ? "失败"
                                : call.status || "未知"}
                          </span>
                        </td>
                        <td>
                          {call.usage?.input_tokens ??
                            call.input_tokens ??
                            "未知"}
                        </td>
                        <td>
                          {call.usage?.output_tokens ??
                            call.output_tokens ??
                            "未知"}
                        </td>
                        <td>
                          <span className="usage-source">
                            {tokenSource(
                              call.usage?.input_source ||
                                call.input_tokens_source ||
                                call.usage_source ||
                                call.source ||
                                "unknown",
                            )}{" "}
                            /{" "}
                            {tokenSource(
                              call.usage?.output_source ||
                                call.output_tokens_source ||
                                call.usage_source ||
                                call.source ||
                                "unknown",
                            )}
                          </span>
                        </td>
                        <td>
                          <code>{call.run_id || "连接测试"}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {pending && <Modal title="保留未保存的修改？" onClose={() => setPending(null)} busy={busy === "save"}><div className="modal-body"><p>当前模型配置有修改，选择保存后继续，或放弃这些修改。</p><Notice error>{error}</Notice>{busy === "save" && <Busy label="正在保存配置" />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setPending(null)}>继续编辑</button><button className="button secondary" disabled={!!busy} onClick={() => { const action = pending.run; setPending(null); action(); }}>放弃修改</button><button className="button primary" disabled={!!busy} onClick={async () => { const action = pending.run; if (await save()) { setPending(null); action(); } }}>保存后继续</button></div></Modal>}
      {confirmDelete && <Modal title="删除模型配置" onClose={() => setConfirmDelete(false)} busy={busy === "delete"}><div className="modal-body"><p>删除“{draft?.name}”及其已存凭据，历史调用记录保留。</p><Notice error>{error}</Notice>{busy === "delete" && <Busy label="正在删除配置" />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setConfirmDelete(false)}>取消</button><button className="button destructive" disabled={!!busy} onClick={() => void remove()}>确认删除</button></div></Modal>}
      {confirmClear && <Modal title="清除已存凭据" onClose={() => setConfirmClear(false)} busy={busy === "clear"}><div className="modal-body"><p>清除该连接的会话密钥、系统密钥库凭据和环境变量引用，系统环境变量保持原样。</p><Notice error>{error}</Notice>{busy === "clear" && <Busy label="正在清除已存凭据" />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setConfirmClear(false)}>取消</button><button className="button destructive" disabled={!!busy} onClick={() => void clearCredentials()}>确认清除</button></div></Modal>}
    </main>
  );
}
