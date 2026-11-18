import { useEffect, useState } from "react";
import { ArrowRight, X } from "lucide-react";
import { send, studio, type ModelProfile } from "./studio";
import { Busy, Notice, messageOf } from "./ui";
import "./research.css";

export type SearchSettings = { provider: "tavily" | "brave" | "searxng"; endpoint: string; credential_mode: "session" | "vault" | "environment"; key_env: string; key_available: boolean; target_fingerprint: string };
const endpoints = { tavily: "https://api.tavily.com/search", brave: "https://api.search.brave.com/res/v1/web/search", searxng: "http://127.0.0.1:8080/search" };
type Props = { settings?: SearchSettings | null; onSave: (settings: SearchSettings) => void; onClose: () => void; models?: ModelProfile[]; modelId?: string; onModel?: (value: string) => void; embeddingId?: string; embeddingModel?: string; onEmbedding?: (value: string) => void; onEmbeddingModel?: (value: string) => void; onOpenModels?: () => void; hideHeading?: boolean; onBusy?: (busy: boolean) => void };
export default function SearchConfiguration({ settings, onSave, onClose, models = [], modelId = "", onModel, embeddingId = "", embeddingModel = "", onEmbedding, onEmbeddingModel, onOpenModels, hideHeading = false, onBusy }: Props) {
  const [draft, setDraft] = useState<SearchSettings>(settings || { provider: "tavily", endpoint: endpoints.tavily, credential_mode: "session", key_env: "", key_available: false, target_fingerprint: "" });
  const [key, setKey] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => { if (settings) return; const controller = new AbortController(); setBusy(true); void studio<SearchSettings>("/research/settings", { signal: controller.signal }).then(setDraft).catch(e => { if (!controller.signal.aborted) setError(messageOf(e)); }).finally(() => { if (!controller.signal.aborted) setBusy(false); }); return () => controller.abort(); }, [settings]);
  useEffect(() => { onBusy?.(busy); }, [busy, onBusy]);
  const close = () => { setKey(""); onClose(); };
  const save = async () => { setBusy(true); setError(""); try { const next = await send<SearchSettings>("/research/settings", { provider: draft.provider, endpoint: draft.endpoint, credential_mode: draft.credential_mode, key_env: draft.key_env, ...(key ? { api_key: key } : {}) }, "PUT"); setKey(""); onSave(next); } catch (e) { setError(messageOf(e)); } finally { setBusy(false); } };
  return <section className="research-config-pane">
    {!hideHeading && <header className="research-config-heading"><h3>{onModel ? "模型与搜索" : "搜索配置"}</h3><button className="icon-button" disabled={busy} aria-label="返回" onClick={close}><X size={17} /></button></header>}
    {busy && <Busy label="正在处理搜索配置" />}
    <fieldset className="research-config" disabled={busy}>
      {onModel && <label className="studio-field"><span>资料模型</span><select aria-label="资料模型" value={modelId} onChange={e => onModel(e.target.value)}><option value="">选择模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.model}</option>)}</select></label>}
      {onEmbedding && <label className="studio-field"><span>向量模型连接</span><select aria-label="向量模型连接" value={embeddingId} onChange={e => onEmbedding(e.target.value)}><option value="">选择连接</option>{models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>}
      {onEmbeddingModel && <label className="studio-field"><span>向量模型名称</span><input aria-label="向量模型名称" value={embeddingModel} onChange={e => onEmbeddingModel(e.target.value)} /></label>}
      {onOpenModels && <button className="text-button" onClick={() => { close(); onOpenModels(); }}>模型设置<ArrowRight size={13} /></button>}
      {onModel && <div className="research-config-divider" />}
      <label className="studio-field"><span>搜索服务</span><select aria-label="搜索服务" value={draft.provider} onChange={e => { const provider = e.target.value as SearchSettings["provider"]; setDraft({ ...draft, provider, endpoint: endpoints[provider] }); }}><option value="tavily">Tavily</option><option value="brave">Brave Search</option><option value="searxng">SearXNG</option></select></label>
      <label className="studio-field"><span>搜索地址</span><input aria-label="搜索地址" value={draft.endpoint} onChange={e => setDraft({ ...draft, endpoint: e.target.value })} /></label>
      {draft.provider !== "searxng" && <><label className="studio-field"><span>密钥保存</span><select aria-label="密钥保存" value={draft.credential_mode} onChange={e => setDraft({ ...draft, credential_mode: e.target.value as SearchSettings["credential_mode"] })}><option value="session">当前会话</option><option value="vault">系统凭据</option><option value="environment">环境变量</option></select></label>{draft.credential_mode === "environment" ? <label className="studio-field"><span>环境变量名</span><input aria-label="环境变量名" value={draft.key_env} onChange={e => setDraft({ ...draft, key_env: e.target.value })} /></label> : <label className="studio-field"><span>搜索 API Key</span><input aria-label="搜索 API Key" type="password" autoComplete="new-password" value={key} placeholder={draft.key_available ? "已配置" : ""} onChange={e => setKey(e.target.value)} /></label>}</>}
      <Notice error>{error}</Notice>
    </fieldset>
    <footer className="modal-footer"><button className="button secondary" disabled={busy} onClick={close}>取消</button><button className="button primary" disabled={busy} onClick={() => void save()}>保存</button></footer>
  </section>;
}
