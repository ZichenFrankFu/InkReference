import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Check, ChevronRight, FileInput, Layers3, Plus, Save, Settings2, Sparkles, Upload } from "lucide-react";
import { studio, send, normalizeList, type Workflow, type Skill, type ModelProfile, type List } from "./studio";
import { Busy, Empty, Modal, Notice, PageTitle, SelectField, messageOf } from "./ui";
import "./workflow-settings.css";

type Stage = "plot" | "characters" | "settings" | "language";
const stages: { id: Stage; name: string; output: string }[] = [
  { id: "plot", name: "剧情大纲", output: "事件 · 情节线 · 卷级与全书大纲" },
  { id: "characters", name: "角色", output: "角色 · 阶段状态 · 事件经历" },
  { id: "settings", name: "设定", output: "设定 · 属性变化 · 认识修正" },
  { id: "language", name: "语言分析", output: "语言风格 · 剧情分析 · 时代背景" },
];
const steps = ["输入文件", "数据清洗", "分卷与章节", ...stages.map(stage => stage.name)];
const clean = <T extends { builtin?: boolean }>(value: T): T => { const result = structuredClone(value); delete result.builtin; return result; };
const fingerprint = (value: unknown) => JSON.stringify(value);
const nextVersion = (version: string) => { const match = version.match(/^(\d+)\.(\d+)\.(\d+)$/); return match ? `${match[1]}.${match[2]}.${Number(match[3]) + 1}` : `${version}-copy`; };

export default function Workflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [skill, setSkill] = useState<Skill | null>(null);
  const [workflowBase, setWorkflowBase] = useState("");
  const [skillBase, setSkillBase] = useState("");
  const [tab, setTab] = useState<"agents" | "skills">("agents");
  const [stage, setStage] = useState<Stage>("plot");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saveKind, setSaveKind] = useState<"workflow" | "skill" | null>(null);
  const [identity, setIdentity] = useState({ id: "", version: "", name: "" });
  const [pending, setPending] = useState<(() => void) | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const workflowDirty = !!workflow && fingerprint(clean(workflow)) !== workflowBase;
  const skillDirty = !!skill && fingerprint(clean(skill)) !== skillBase;
  const dirty = workflowDirty || skillDirty;
  const fixedWorkflows = workflows.filter(item => item.fixed_pipeline);
  const fixedSkills = skills.filter(item => item.contract_version === 2);
  const chooseWorkflow = (item: Workflow) => { setWorkflow(structuredClone(item)); setWorkflowBase(fingerprint(clean(item))); setError(""); };
  const chooseSkill = (item: Skill) => { setSkill(structuredClone(item)); setSkillBase(fingerprint(clean(item))); setError(""); };
  const load = useCallback(async () => {
    const [w, s, p] = await Promise.all([studio<List<Workflow>>("/workflows"), studio<List<Skill>>("/skills"), studio<List<ModelProfile>>("/models")]);
    const ws = normalizeList(w), ss = normalizeList(s); setWorkflows(ws); setSkills(ss); setProfiles(normalizeList(p));
    return { ws, ss };
  }, []);
  useEffect(() => { let live = true; void load().then(({ ws, ss }) => { if (!live) return; const w = ws.find(item => item.fixed_pipeline); const s = ss.find(item => item.contract_version === 2); if (w) chooseWorkflow(w); if (s) chooseSkill(s); }).catch(e => { if (live) setError(messageOf(e)); }).finally(() => { if (live) setLoading(false); }); return () => { live = false; }; }, [load]);
  useEffect(() => {
    const refresh = () => { setBusy(current => current || "refresh"); void load().catch(e => setError(messageOf(e))).finally(() => setBusy(current => current === "refresh" ? "" : current)); };
    const refreshModels = () => { void studio<List<ModelProfile>>("/models").then(data => setProfiles(normalizeList(data))).catch(e => setError(messageOf(e))); };
    window.addEventListener("inkreference:workflows-refresh", refresh);
    window.addEventListener("inkreference:models-changed", refreshModels);
    return () => { window.removeEventListener("inkreference:workflows-refresh", refresh); window.removeEventListener("inkreference:models-changed", refreshModels); };
  }, [load]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [dirty]);
  const guard = (action: () => void) => { if (dirty) setPending(() => action); else action(); };
  const update = (patch: Partial<Workflow>) => setWorkflow(value => value ? { ...value, ...patch } : value);
  const openSave = (kind: "workflow" | "skill", fresh = false) => {
    const value = kind === "workflow" ? workflow : skill; if (!value) return;
    const unsavedSkill = kind === "skill" && !skillBase;
    setIdentity({ id: fresh || value.builtin ? kind === "workflow" ? "my-analysis" : "my-skill" : value.id, version: fresh || value.builtin ? "1.0.0" : unsavedSkill ? value.version : nextVersion(value.version), name: value.name });
    setSaveKind(kind); setError("");
  };
  const save = async () => {
    const source = saveKind === "workflow" ? workflow : skill; if (!source || !saveKind) return;
    setBusy("save"); setError(""); setMessage("");
    try {
      const payload = { ...clean(source), ...identity };
      const result = await send<Workflow | Skill>(saveKind === "workflow" ? "/workflows" : "/skills", payload);
      await load();
      if (saveKind === "workflow") chooseWorkflow(result as Workflow); else chooseSkill(result as Skill);
      setSaveKind(null); setMessage("已保存新版本");
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const uploadSkill = async (file: File) => {
    setBusy("upload"); setError(""); setMessage("");
    try { const body = new FormData(); body.append("file", file); const result = await studio<Skill>("/skills/upload?contract_version=2", { method: "POST", body }); await load(); chooseSkill(result); setTab("skills"); setMessage("Skill 已导入"); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); if (upload.current) upload.current.value = ""; }
  };
  const stageConfig = workflow?.stage_settings?.[stage] || {};
  const selectedStage = stages.find(item => item.id === stage)!;
  return <main className="studio-main agent-settings-main">
    <PageTitle title="Agent 与 Skill" action={<div className="segmented" role="tablist" aria-label="Agent 与 Skill"><button role="tab" aria-selected={tab === "agents"} className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}><Settings2 size={14} />Agent</button><button role="tab" aria-selected={tab === "skills"} className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}><Sparkles size={14} />Skills</button></div>} />
    {(loading || busy) && <Busy label={loading || busy === "refresh" ? "正在读取配置" : busy === "upload" ? "正在导入 Skill" : "正在保存配置"} />}
    <div className="fixed-pipeline" aria-label="固定作品处理流程">{steps.map((label, index) => <span key={label}><i>{index + 1}</i>{label}{index < steps.length - 1 && <ChevronRight size={12} />}</span>)}</div>
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    {tab === "agents" ? workflow ? <section className="surface fixed-agent-editor">
      <div className="agent-config-heading"><SelectField label="分析方案" value={`${workflow.id}@${workflow.version}`} onChange={key => guard(() => { const found = fixedWorkflows.find(item => `${item.id}@${item.version}` === key); if (found) chooseWorkflow(found); })}>{fixedWorkflows.map(item => <option key={`${item.id}@${item.version}`} value={`${item.id}@${item.version}`}>{item.name} · v{item.version}</option>)}</SelectField><button className="button secondary" onClick={() => openSave("workflow", true)} disabled={!!busy}><Plus size={15} />另存方案</button><button className="button primary" disabled={!workflowDirty || !!busy} onClick={() => openSave("workflow")}><Save size={15} />保存新版本</button></div>
      <div className="agent-stage-layout"><nav className="agent-stage-list" aria-label="提取阶段">{stages.map((item, index) => <button className={item.id === stage ? "active" : ""} key={item.id} onClick={() => setStage(item.id)}><span>{index + 4}</span><strong>{item.name}</strong><ChevronRight size={14} /></button>)}</nav>
        <div className="agent-stage-content"><header><div><h2>{selectedStage.name}</h2><span className="tag">Schema 2</span></div><p className="agent-output-fields">{selectedStage.output}</p></header>
          <SelectField label="Skill" value={`${workflow.stage_skills?.[stage]?.id || workflow.skill_id}@${workflow.stage_skills?.[stage]?.version || workflow.skill_version}`} onChange={key => { const value = fixedSkills.find(item => `${item.id}@${item.version}` === key); if (value) update({ stage_skills: { ...workflow.stage_skills, [stage]: { id: value.id, version: value.version } } }); }}>{fixedSkills.map(item => <option value={`${item.id}@${item.version}`} key={`${item.id}@${item.version}`}>{item.name} · v{item.version}</option>)}</SelectField>
          <SelectField label="模型" value={workflow.stage_models?.[stage] || ""} onChange={profileId => { const overrides = { ...workflow.stage_models }; if (profileId) overrides[stage] = profileId; else delete overrides[stage]; update({ stage_models: overrides }); }}><option value="">使用任务模型</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}</SelectField>
          <label className="studio-field"><span>提取要求</span><textarea aria-label={`${selectedStage.name}提取要求`} rows={10} value={workflow.stage_prompts?.[stage] || ""} onChange={e => update({ stage_prompts: { ...workflow.stage_prompts, [stage]: e.target.value } })} /></label>
          <details className="agent-settings-details"><summary><Settings2 size={15} />阶段用量</summary><div className="agent-number-grid"><label className="studio-field"><span>最大输出 Token</span><input type="number" min={64} max={128000} value={stageConfig.max_output_tokens ?? workflow.max_output_tokens} onChange={e => update({ stage_settings: { ...workflow.stage_settings, [stage]: { ...stageConfig, max_output_tokens: Number(e.target.value) } } })} /></label><label className="studio-field"><span>阶段 Token 预算</span><input type="number" min={1} max={100000000} value={stageConfig.budget_tokens ?? workflow.budget_tokens} onChange={e => update({ stage_settings: { ...workflow.stage_settings, [stage]: { ...stageConfig, budget_tokens: Number(e.target.value) } } })} /></label></div></details>
        </div></div>
      <details className="agent-settings-details agent-global-settings"><summary><Layers3 size={15} />分块与总用量</summary><div className="agent-number-grid">{([["chunk_chars", "分块字数上限", 100, 100000], ["max_input_tokens", "单次输入 Token 上限", 128, 2000000], ["max_output_tokens", "默认输出 Token 上限", 64, 128000], ["budget_tokens", "任务 Token 总预算", 1, 100000000], ["retry_limit", "失败重试次数", 0, 8]] as const).map(([key, label, min, max]) => <label className="studio-field" key={key}><span>{label}</span><input aria-label={label} type="number" min={min} max={max} value={workflow[key]} onChange={e => update({ [key]: Number(e.target.value) })} /></label>)}</div><label className="check-line" style={{ padding: "0 16px 16px" }}><input type="checkbox" checked={workflow.reuse_candidates !== false} onChange={e => update({ reuse_candidates: e.target.checked })} />复用完全相同的候选</label><label className="studio-field"><span>全局提取要求</span><textarea rows={4} value={workflow.custom_prompt} onChange={e => update({ custom_prompt: e.target.value })} /></label></details>
      {workflowDirty && <div className="agent-draft-status"><span className="unsaved"><span />未保存</span></div>}
    </section> : !loading ? <Empty icon={BookOpen} title="没有可用分析方案" /> : null : <section className="surface skill-content-editor">
      <div className="agent-config-heading"><SelectField label="Skill" value={skill ? `${skill.id}@${skill.version}` : ""} onChange={key => guard(() => { const found = fixedSkills.find(item => `${item.id}@${item.version}` === key); if (found) chooseSkill(found); })}>{fixedSkills.map(item => <option value={`${item.id}@${item.version}`} key={`${item.id}@${item.version}`}>{item.name} · v{item.version}</option>)}{skill && !fixedSkills.some(item => item.id === skill.id && item.version === skill.version) && <option value={`${skill.id}@${skill.version}`}>{skill.name || "新 Skill"}</option>}</SelectField><button className="button secondary" disabled={!!busy} onClick={() => guard(() => { const base = fixedSkills.find(item => item.builtin) || fixedSkills[0]; if (base) { setSkill({ ...clean(base), id: "my-skill", version: "1.0.0", name: "我的 Skill", description: "", prompt: "" }); setSkillBase(""); } })}><Plus size={15} />添加 Skill</button><button className="button secondary" disabled={!!busy} onClick={() => guard(() => upload.current?.click())}><Upload size={15} />上传 Skill</button><input ref={upload} hidden type="file" accept=".json,.zip,.md" onChange={e => { if (e.target.files?.[0]) void uploadSkill(e.target.files[0]); }} /></div>
      {skill ? <div className="skill-content-fields"><div className="agent-number-grid"><label className="studio-field"><span>名称</span><input value={skill.name} onChange={e => setSkill({ ...skill, name: e.target.value })} /></label><label className="studio-field"><span>描述</span><input value={skill.description} onChange={e => setSkill({ ...skill, description: e.target.value })} /></label></div><label className="studio-field"><span>提取要求与示例</span><textarea rows={15} aria-label="Skill 提取要求与示例" value={skill.prompt} onChange={e => setSkill({ ...skill, prompt: e.target.value })} /></label><details className="agent-settings-details"><summary><FileInput size={15} />输出结构 · Schema 2</summary><pre className="readonly-schema">{JSON.stringify(skill.output_schema, null, 2)}</pre></details><div className="agent-save-row">{skillDirty && <span className="unsaved"><span />未保存</span>}<button className="button primary" disabled={!skillDirty || !!busy || !skill.prompt.trim()} onClick={() => openSave("skill")}><Save size={15} />保存新版本</button></div></div> : !loading ? <Empty icon={Sparkles} title="尚无 Skill" /> : null}
    </section>}
    {saveKind && <Modal title={saveKind === "workflow" ? "保存分析方案" : "保存 Skill"} busy={!!busy} onClose={() => setSaveKind(null)}><div className="modal-body"><label className="studio-field"><span>名称</span><input value={identity.name} onChange={e => setIdentity({ ...identity, name: e.target.value })} /></label><label className="studio-field"><span>ID</span><input value={identity.id} onChange={e => setIdentity({ ...identity, id: e.target.value })} /></label><label className="studio-field"><span>版本</span><input value={identity.version} onChange={e => setIdentity({ ...identity, version: e.target.value })} /></label><Notice error>{error}</Notice></div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setSaveKind(null)}>取消</button><button className="button primary" disabled={!!busy || !identity.name.trim() || !identity.id || !identity.version} onClick={() => void save()}><Check size={15} />保存</button></div></Modal>}
    {pending && <Modal title="配置尚未保存" onClose={() => setPending(null)}><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = pending; setPending(null); if (workflowBase) { const base = JSON.parse(workflowBase) as Workflow; chooseWorkflow(workflows.find(item => item.id === base.id && item.version === base.version) || base); } const baseSkill = skillBase ? JSON.parse(skillBase) as Skill : fixedSkills[0]; if (baseSkill) chooseSkill(skills.find(item => item.id === baseSkill.id && item.version === baseSkill.version) || baseSkill); action(); }}>放弃修改</button></div></Modal>}
  </main>;
}
