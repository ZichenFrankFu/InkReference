import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Check, ClipboardPaste, Copy, PencilLine, Sparkles } from "lucide-react";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { send, studio, type ModelProfile } from "./studio";
import { ActivityRail } from "./activity";
import { Modal, Notice, messageOf } from "./ui";
import "./dynamic-function.css";

export type DynamicMode = "automatic" | "semi" | "manual";
export type DynamicModeOption<T extends string = DynamicMode> = { value: T; label: string; disabled?: boolean };
const defaultModes: DynamicModeOption[] = [
  { value: "automatic", label: "自动" },
  { value: "semi", label: "半自动" },
  { value: "manual", label: "手动" },
];

/** Controlled presentation shared by decisions; domain services retain all writes. */
export function DynamicModeSwitch<T extends string = DynamicMode>({ value, onChange, disabled = false, items, label = "交互模式" }: {
  value: T;
  onChange: (mode: T) => void;
  disabled?: boolean;
  items?: DynamicModeOption<T>[];
  label?: string;
}) {
  const modes = items || defaultModes as DynamicModeOption<T>[];
  return <div className="dynamic-mode-switch" role="group" aria-label={label}>
    {modes.map((item, index) => {
      const Icon = [Sparkles, ClipboardPaste, PencilLine][index] || PencilLine;
      return <button type="button" key={item.value} aria-pressed={value === item.value} className={value === item.value ? "active" : ""} disabled={disabled || item.disabled} onClick={() => onChange(item.value)}><Icon size={14} /><span>{item.label}</span></button>;
    })}
  </div>;
}

export function DynamicPromptExchange({ prompt, reply, onReplyChange, onParse, disabled = false, promptLabel = "提示词", replyLabel = "模型回复", parseLabel = "解析回复", placeholder = '{"protocol":"…"}', extra }: {
  prompt: string;
  reply: string;
  onReplyChange: (value: string) => void;
  onParse?: () => void;
  disabled?: boolean;
  promptLabel?: string;
  replyLabel?: string;
  parseLabel?: string;
  placeholder?: string;
  extra?: ReactNode;
}) {
  const promptId = useId();
  const ref = useRef<HTMLTextAreaElement>(null);
  const [copyState, setCopyState] = useState<"" | "copied" | "selected">("");
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(prompt);
      setCopyState("copied");
    } catch {
      ref.current?.focus(); ref.current?.select(); setCopyState("selected");
    }
  };
  return <section className="dynamic-prompt-exchange">
    <div className="dynamic-field-heading"><label htmlFor={promptId}>{promptLabel}</label><button className="button secondary compact-button" type="button" disabled={!prompt || disabled} onClick={() => void copy()}>{copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}{copyState === "copied" ? "已复制" : "复制提示词"}</button></div>
    <textarea id={promptId} ref={ref} aria-label={promptLabel} className="dynamic-prompt-text" value={prompt} readOnly spellCheck={false} />
    {copyState === "selected" && <span className="dynamic-copy-status" role="status">已选中 · Ctrl+C / ⌘C</span>}
    {extra}
    <label className="studio-field dynamic-reply"><span>{replyLabel}</span><textarea aria-label={replyLabel} value={reply} onChange={event => onReplyChange(event.target.value)} disabled={disabled} placeholder={placeholder} spellCheck={false} /></label>
    {onParse && <div className="dynamic-exchange-actions"><button type="button" className="button primary" disabled={disabled || !reply.trim()} onClick={onParse}>{parseLabel}</button></div>}
  </section>;
}

export function DynamicModelSelector({ profiles, value, onChange, disabled = false, label = "模型", onConfigure }: {
  profiles: ModelProfile[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  label?: string;
  onConfigure?: () => void;
}) {
  const selected = profiles.find(profile => profile.id === value);
  return <div className="dynamic-model-selector"><div className="dynamic-model-row"><label className="studio-field"><span>{label}</span><select aria-label={label} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}><option value="">选择模型</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}</select></label>{onConfigure && <button type="button" className="text-button" disabled={disabled} onClick={onConfigure}>模型设置</button>}</div>{selected && <div className="dynamic-model-target"><span>{isRemoteModel(selected) ? "远程 API" : "本机模型"}</span><strong>{selected.model}</strong><small>{modelEndpoint(selected.base_url)}</small></div>}</div>;
}

export function DynamicActionRail({ children, leading, busy = false, label = "正在处理" }: { children: ReactNode; leading?: ReactNode; busy?: boolean; label?: string }) {
  return <div className="dynamic-action-area"><ActivityRail active={busy} label={label} /><div className="dynamic-action-rail"><div>{leading}</div><div>{children}</div></div></div>;
}

export type PromptWorkbenchModalProps = {
  title: string; mode: "automatic" | "semi";
  prompt: string; onPromptChange: (value: string) => void;
  reply: string; onReplyChange: (value: string) => void;
  profiles: ModelProfile[]; profileId: string; onProfileChange: (id: string) => void;
  onSend: () => void | Promise<void>; onPreview: () => void | Promise<void>;
  onApply: () => void | Promise<void>; onClose: () => void;
  busy?: boolean; error?: string; preview?: ReactNode; canApply?: boolean;
  applyLabel?: string; previewLabel?: string; children?: ReactNode;
};
export function PromptWorkbenchModal({ title, mode, prompt, onPromptChange, reply, onReplyChange, profiles, profileId, onProfileChange, onSend, onPreview, onApply, onClose, busy = false, error, preview, canApply = false, applyLabel = "确认应用", previewLabel = "解析回复", children }: PromptWorkbenchModalProps) {
  const promptId = useId(), replyId = useId();
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(prompt); setCopied(true); } catch { promptRef.current?.focus(); promptRef.current?.select(); } };
  return <Modal title={title} onClose={onClose} busy={busy} wide>
    <div className="modal-body prompt-workbench">
      <DynamicModelSelector profiles={profiles} value={profileId} onChange={onProfileChange} disabled={busy} />
      {children}
      <div className="prompt-workbench-columns">
        <section><div className="dynamic-field-heading"><label htmlFor={promptId}>提示词</label><button type="button" className="button secondary compact-button" disabled={busy || !prompt} onClick={() => void copy()}>{copied ? <Check size={14}/> : <Copy size={14}/>}{copied ? "已复制" : "复制提示词"}</button></div><textarea id={promptId} ref={promptRef} aria-label="提示词" value={prompt} onChange={event => { setCopied(false); onPromptChange(event.target.value); }} disabled={busy} spellCheck={false}/></section>
        <section><div className="dynamic-field-heading"><label htmlFor={replyId}>{mode === "automatic" ? "模型回复" : "粘贴模型回复"}</label></div><textarea id={replyId} aria-label={mode === "automatic" ? "模型回复" : "粘贴模型回复"} value={reply} onChange={event => onReplyChange(event.target.value)} disabled={busy} spellCheck={false}/></section>
      </div>
      <Notice error>{error}</Notice><ActivityRail active={busy} label="正在处理" />
      {preview && <section className="prompt-workbench-preview">{preview}</section>}
    </div>
    <div className="modal-footer prompt-workbench-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>关闭</button><div><button type="button" className="button secondary" disabled={busy || !prompt.trim() || !profileId} onClick={() => void onSend()}>确认发送</button><button type="button" className="button secondary" disabled={busy || !reply.trim()} onClick={() => void onPreview()}>{previewLabel}</button><button type="button" className="button primary" disabled={busy || !canApply} onClick={() => void onApply()}>{applyLabel}</button></div></div>
  </Modal>;
}

export type DynamicPrepared = { prepared_id: string; prompt: string; profile_id?: string; model_fingerprint?: string; embedding_fingerprint?: string; embedding_profile_id?: string; embedding_remote?: boolean };
export function usePromptWorkbench<T>({ operation, title, mode, profiles, profileId, onProfileChange, onApply, renderPreview, applyLabel, resumeScope, completeOnApply = true }: {
  operation: "catalog" | "volumes" | "extraction" | "repair" | "summarize" | "translate";
  title: string; mode: "automatic" | "semi"; profiles: ModelProfile[]; profileId: string;
  onProfileChange: (id: string) => void; onApply: (result: T, preparedId: string, prepared: DynamicPrepared | null) => Promise<void>;
  renderPreview?: (result: T) => ReactNode; applyLabel?: string; resumeScope?: Record<string, string | number | undefined>; completeOnApply?: boolean;
}) {
  const [opened, setOpened] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [activeMode, setActiveMode] = useState(mode);
  const [prepared, setPrepared] = useState<DynamicPrepared | null>(null), [payload, setPayload] = useState<Record<string, unknown>>({});
  const [prompt, setPrompt] = useState(""), [reply, setReply] = useState(""), [result, setResult] = useState<T | null>(null);
  const prepare = async (body: Record<string, unknown>, id: string, requestMode = activeMode) => send<DynamicPrepared>(`/dynamic-functions/${operation}/prepare`, { payload: body, ...(id ? { profile_id: id } : {}), mode: requestMode });
  const open = async (body: Record<string, unknown>, selectedProfileId = profileId) => {
    setActiveMode(mode); if (selectedProfileId !== profileId) onProfileChange(selectedProfileId); setOpened(true); setBusy(true); setError(""); setPayload(body); setPrepared(null); setPrompt(""); setReply(""); setResult(null);
    try { const next = await prepare(body, selectedProfileId, mode); setPrepared(next); setPrompt(next.prompt); } catch (failure) { setError(messageOf(failure)); } finally { setBusy(false); }
  };
  const resume = async (id: string) => {
    try {
      const saved = await studio<DynamicPrepared & { operation: string; mode?: "automatic" | "semi"; payload: Record<string, unknown>; binding?: Record<string, unknown>; raw_reply?: string; reply?: string }>(`/dynamic-functions/preparations/${encodeURIComponent(id)}`);
      if (saved.operation !== operation || (resumeScope && Object.entries(resumeScope).some(([key, value]) => value === undefined || String(saved.payload[key] ?? saved.binding?.[key] ?? "") !== String(value)))) return false;
      setActiveMode(saved.mode || mode); setOpened(true); setBusy(true); setError(""); setResult(null); setPrepared(saved); setPayload(saved.payload); setPrompt(saved.prompt); setReply(saved.raw_reply ?? saved.reply ?? ""); if (saved.profile_id) onProfileChange(saved.profile_id);
      await send(`/dynamic-functions/preparations/${encodeURIComponent(id)}/control`, { action: "resume" }); return true;
    } catch (failure) { setPrepared(null); setPrompt(""); setReply(""); setResult(null); setOpened(true); setError(messageOf(failure)); return false; } finally { setBusy(false); }
  };
  const scopeKey = JSON.stringify(resumeScope || {});
  useEffect(() => {
    const focus = (event?: Event) => { let target: { operation?: string; prepared_id?: string } | null = null; try { target = event ? (event as CustomEvent).detail : JSON.parse(sessionStorage.getItem("inkreference.prompt-focus") || "null"); } catch { /* Optional navigation state. */ } if (target?.operation === operation && target.prepared_id) { const id = target.prepared_id; void resume(id).then(restored => { if (restored) sessionStorage.removeItem("inkreference.prompt-focus"); }); } };
    focus(); window.addEventListener("inkreference:prompt-focus", focus); return () => window.removeEventListener("inkreference:prompt-focus", focus);
  // The owning document/task must match the preparation before consuming it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation, scopeKey]);
  const selectProfile = async (id: string) => {
    onProfileChange(id); setBusy(true); setError(""); setResult(null);
    try { const next = await prepare(payload, id); setPrepared(next); setPrompt(next.prompt); setReply(""); } catch (failure) { setPrepared(null); setError(messageOf(failure)); } finally { setBusy(false); }
  };
  const execute = async () => {
    if (!prepared) return; setBusy(true); setError(""); setResult(null);
    try {
      const profile = profiles.find(item => item.id === profileId);
      const next = await send<{ raw_reply?: string; reply?: string; content?: string }>(`/dynamic-functions/${operation}/execute`, { prepared_id: prepared.prepared_id, prompt_override: prompt, model_profile_id: profileId, remote_consent: isRemoteModel(profile), expected_model_fingerprint: prepared.model_fingerprint || profile?.target_fingerprint });
      setReply(next.raw_reply ?? next.reply ?? next.content ?? "");
    } catch (failure) { setError(messageOf(failure)); } finally { setBusy(false); }
  };
  const parse = async () => {
    if (!prepared) return; setBusy(true); setError(""); setResult(null);
    try { const next = await send<{ domain_result: T }>(`/dynamic-functions/${operation}/preview`, { prepared_id: prepared.prepared_id, reply }); setResult(next.domain_result); } catch (failure) { setError(messageOf(failure)); } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!result) return; setBusy(true); setError("");
    try { await onApply(result, prepared?.prepared_id || "", prepared); if (prepared && completeOnApply) await send(`/dynamic-functions/preparations/${encodeURIComponent(prepared.prepared_id)}/control`, { action: "complete" }); setOpened(false); } catch (failure) { setError(messageOf(failure)); } finally { setBusy(false); }
  };
  const modal = opened ? <PromptWorkbenchModal title={title} mode={activeMode} prompt={prompt} onPromptChange={value => { setPrompt(value); setResult(null); }} reply={reply} onReplyChange={value => { setReply(value); setResult(null); }} profiles={profiles} profileId={profileId} onProfileChange={id => void selectProfile(id)} onSend={execute} onPreview={parse} onApply={apply} onClose={() => setOpened(false)} busy={busy} error={error} canApply={!!result} applyLabel={applyLabel} preview={result ? renderPreview ? renderPreview(result) : <pre>{JSON.stringify(result, null, 2)}</pre> : undefined} /> : null;
  return { open, resume, modal, busy, opened };
}
