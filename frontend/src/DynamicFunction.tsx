import { useId, useRef, useState, type ReactNode } from "react";
import { Check, ClipboardPaste, Copy, PencilLine, Sparkles } from "lucide-react";
import { isRemoteModel, modelEndpoint } from "./model-security";
import type { ModelProfile } from "./studio";
import { ActivityRail } from "./activity";
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
