import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  Position,
  applyNodeChanges,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Check,
  Code2,
  FileCheck2,
  FileText,
  GitBranch,
  Layers3,
  Plus,
  Save,
  Settings2,
  Sparkles,
  Upload,
} from "lucide-react";
import {
  studio,
  send,
  normalizeList,
  describe,
  type Workflow,
  type Skill,
  type List,
} from "./studio";
import {
  Busy,
  Empty,
  Modal,
  Notice,
  PageTitle,
  SelectField,
  messageOf,
} from "./ui";
const newWorkflow: Workflow = {
  id: "my-workflow",
  version: "1.0.0",
  name: "我的分析流程",
  skill_id: "unified-reference",
  skill_version: "1.0.0",
  nodes: { summary: true, characters: true, events: true, cliches: true },
  custom_prompt: "",
  chunk_chars: 6000,
  retry_limit: 2,
  max_input_tokens: 12000,
  max_output_tokens: 3000,
  budget_tokens: 100000,
};
const newSkill: Skill = {
  id: "my-skill",
  version: "1.0.0",
  name: "我的分析 Skill",
  description: "",
  prompt: "",
  output_schema: {
    type: "object",
    properties: {
      summary: { type: "string" },
      characters: {
        type: "array",
        items: {
          type: "object",
          properties: {
            name: { type: "string" },
            description: { type: "string" },
            evidence: { type: "string" },
          },
          required: ["name", "description", "evidence"],
        },
      },
      ...Object.fromEntries(
        ["events", "cliches"].map((key) => [
          key,
          {
            type: "array",
            items: {
              type: "object",
              properties: {
                name: { type: "string" },
                description: { type: "string" },
                evidence: { type: "string" },
              },
              required: ["name", "description", "evidence"],
            },
          },
        ]),
      ),
    },
    required: ["summary", "characters", "events", "cliches"],
  },
};
const outputNames: Record<string, string> = {
  summary: "剧情梗概",
  characters: "人物事实",
  events: "剧情事件",
  cliches: "桥段实例",
};
function nextVersion(version: string) {
  const parts = version.split(".");
  if (parts.length === 3 && !isNaN(Number(parts[2])))
    return `${parts[0]}.${parts[1]}.${Number(parts[2]) + 1}`;
  return `${version}-copy`;
}
function buildNodes(
  workflow: Workflow,
  positions: Record<string, { x: number; y: number }>,
): Node[] {
  const base = [
    {
      id: "source",
      name: "来源全文",
      detail: "固定文本版本",
      icon: FileText,
      x: 15,
      y: 160,
    },
    {
      id: "chunk",
      name: "覆盖切分",
      detail: `每块 ≤ ${workflow.chunk_chars.toLocaleString()} 字`,
      icon: Layers3,
      x: 235,
      y: 160,
    },
    {
      id: "extract",
      name: "统一模型抽取",
      detail: "Skill + 自定义提示词",
      icon: Sparkles,
      x: 455,
      y: 160,
    },
    {
      id: "validate",
      name: "证据与格式校验",
      detail: "连续原文 · 字符位置",
      icon: FileCheck2,
      x: 675,
      y: 160,
    },
    {
      id: "review",
      name: "人工审核",
      detail: "确认后入库",
      icon: Check,
      x: 895,
      y: 160,
    },
  ];
  return base.map((b) => ({
    id: b.id,
    position: positions[b.id] || { x: b.x, y: b.y },
    data: {
      label: (
        <div className={`flow-node flow-${b.id}`}>
          <span className="flow-node-icon">
            <b.icon size={19} />
          </span>
          <strong>{b.name}</strong>
          <small>{b.detail}</small>
          {b.id === "extract" && (
            <div className="flow-output-tags">
              {Object.entries(outputNames)
                .filter(([key]) => workflow.nodes[key])
                .map(([key, name]) => (
                  <span key={key}>{name}</span>
                ))}
            </div>
          )}
        </div>
      ),
    },
    type: "default",
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    style: {
      width: 185,
      padding: 0,
      border: "none",
      background: "transparent",
    },
  }));
}
export default function Workflows() {
  const [tab, setTab] = useState<"workflows" | "skills">("workflows");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [draft, setDraft] = useState<Workflow | null>(null);
  const [skill, setSkill] = useState<Skill | null>(null);
  const [schema, setSchema] = useState("");
  const [nodes, setNodes] = useState<Node[]>([]);
  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [selectedNode, setSelectedNode] = useState("extract");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveId, setSaveId] = useState("");
  const [saveVersion, setSaveVersion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    try {
      const [w, s] = await Promise.all([
        studio<List<Workflow>>("/workflows"),
        studio<List<Skill>>("/skills"),
      ]);
      setWorkflows(normalizeList(w));
      setSkills(normalizeList(s));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const choose = async (w: Workflow) => {
    setBusy("load");
    setError("");
    try {
      const result = await studio<Workflow>(
        `/workflows/${w.id}/versions/${w.version}`,
      );
      setDraft(result);
      let layout: Record<string, { x: number; y: number }> = {};
      try {
        const l = await studio<{
          layout: { positions?: Record<string, { x: number; y: number }> };
        }>(`/workflows/${w.id}/versions/${w.version}/layout`);
        layout = l.layout?.positions || {};
      } catch {
        /* older workflow layout defaults to generated graph */
      }
      setPositions(layout);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  useEffect(() => {
    if (draft) setNodes(buildNodes(draft, positions));
  }, [draft, positions]);
  const edges = useMemo(
    () =>
      ["source", "chunk", "extract", "validate"].map((id, i) => ({
        id: `${id}-${i}`,
        source: id,
        target: ["chunk", "extract", "validate", "review"][i],
        animated: false,
        style: { stroke: "#8eaaa0", strokeWidth: 1.7 },
        type: "smoothstep",
      })),
    [],
  );
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((n) => applyNodeChanges(changes, n));
  }, []);
  const save = async () => {
    if (!draft) return;
    setBusy("save");
    setError("");
    try {
      const result = await send<Workflow>("/workflows", {
        ...draft,
        id: saveId,
        version: saveVersion,
        builtin: undefined,
      });
      const pos = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
      await send(
        `/workflows/${result.id}/versions/${result.version}/layout`,
        { positions: pos },
        "PUT",
      );
      setDraft(result);
      setPositions(pos);
      setSaveOpen(false);
      setMessage("工作流新版本已保存。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const saveLayout = async () => {
    if (!draft) return;
    setBusy("layout");
    setError("");
    try {
      const pos = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
      await send(
        `/workflows/${draft.id}/versions/${draft.version}/layout`,
        { positions: pos },
        "PUT",
      );
      setPositions(pos);
      setMessage("画布布局已保存。");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const saveSkill = async () => {
    if (!skill) return;
    setBusy("skill");
    setError("");
    try {
      let parsed;
      try {
        parsed = JSON.parse(schema);
      } catch {
        throw new Error("输出 Schema 需要是有效的 JSON。");
      }
      const result = await send<Skill>("/skills", {
        ...skill,
        output_schema: parsed,
        builtin: undefined,
      });
      setSkill(result);
      setMessage("Skill 版本已保存。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const upload = async (f: File) => {
    setFile(f);
    setBusy("upload");
    setError("");
    try {
      const form = new FormData();
      form.append("file", f);
      const result = await studio<Skill>("/skills/upload", {
        method: "POST",
        body: form,
      });
      setSkill(result);
      setSchema(describe(result.output_schema));
      setMessage("Skill 已校验并导入。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
      if (uploadRef.current) uploadRef.current.value = "";
    }
  };
  return (
    <main className="studio-main">
      <PageTitle
        kicker="WORKFLOW STUDIO"
        title="工作流与 Skills"
        description="把自己的阅读方法，变成可以重复运行的流程。"
        action={
          <div className="inline-actions">
            {tab === "skills" ? (
              <>
                <input
                  ref={uploadRef}
                  type="file"
                  accept=".json,.zip,.md"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void upload(f);
                  }}
                />
                <button
                  className="button secondary"
                  onClick={() => uploadRef.current?.click()}
                  disabled={!!busy}
                >
                  <Upload size={16} />
                  上传 Skill
                </button>
                <button
                  className="button primary"
                  onClick={() => {
                    setSkill({ ...newSkill });
                    setSchema(describe(newSkill.output_schema));
                  }}
                >
                  <Plus size={16} />
                  新建 Skill
                </button>
              </>
            ) : (
              <button
                className="button primary"
                onClick={() => {
                  setDraft({ ...newWorkflow });
                  setPositions({});
                }}
              >
                <Plus size={16} />
                新建工作流
              </button>
            )}
          </div>
        }
      />
      <div className="page-tabs">
        <button
          className={tab === "workflows" ? "active" : ""}
          onClick={() => setTab("workflows")}
        >
          <GitBranch size={16} />
          工作流
        </button>
        <button
          className={tab === "skills" ? "active" : ""}
          onClick={() => setTab("skills")}
        >
          <Code2 size={16} />
          Skills<span>{skills.length}</span>
        </button>
      </div>
      <Notice error={!!error} success={!error && !!message}>
        {error || message}
      </Notice>
      {busy && (
        <Busy
          label={
            {
              load: "正在读取工作流",
              save: "正在保存新版本",
              layout: "正在保存画布",
              skill: "正在校验并保存 Skill",
              upload: `正在校验 ${file?.name || "Skill"}`,
            }[busy]
          }
          compact
        />
      )}
      {tab === "workflows" ? (
        <div className="workflow-layout">
          <aside className="surface workflow-index">
            <div className="surface-heading">
              <h2>工作流版本</h2>
              <span>{workflows.length}</span>
            </div>
            {loading ? (
              <Busy label="正在读取工作流" />
            ) : (
              workflows.map((w) => (
                <button
                  className={`workflow-list-item ${draft?.id === w.id && draft.version === w.version ? "active" : ""}`}
                  onClick={() => void choose(w)}
                  key={`${w.id}@${w.version}`}
                >
                  <span>
                    <GitBranch size={17} />
                    <strong>{w.name}</strong>
                  </span>
                  <small>
                    v{w.version}
                    {w.builtin ? " · 内置" : ""}
                  </small>
                </button>
              ))
            )}
          </aside>
          <section className="surface workflow-editor">
            {!draft ? (
              <Empty
                icon={GitBranch}
                title="定制你的素材提取方法"
                description="选择工作流，调整产出、提示词与预算。"
              />
            ) : (
              <>
                <div className="workflow-toolbar">
                  <div>
                    <h2>{draft.name}</h2>
                    <span>
                      v{draft.version}
                      {draft.builtin ? " · 内置模板" : ""}
                    </span>
                  </div>
                  <div className="inline-actions">
                    <button
                      className="button secondary"
                      disabled={!!busy}
                      onClick={() => void saveLayout()}
                    >
                      <Save size={14} />
                      保存布局
                    </button>
                    <button
                      className="button primary"
                      disabled={!!busy}
                      onClick={() => {
                        setSaveId(draft.id);
                        setSaveVersion(nextVersion(draft.version));
                        setSaveOpen(true);
                      }}
                    >
                      保存为新版本
                    </button>
                  </div>
                </div>
                <div className="workflow-canvas">
                  <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onNodeClick={(_, node) => setSelectedNode(node.id)}
                    onNodeDragStop={(_, node) =>
                      setPositions((p) => ({ ...p, [node.id]: node.position }))
                    }
                    nodesConnectable={false}
                    edgesReconnectable={false}
                    fitView
                    fitViewOptions={{ padding: 0.14 }}
                    minZoom={0.3}
                    maxZoom={1.5}
                    deleteKeyCode={null}
                    proOptions={{ hideAttribution: false }}
                  >
                    <Background color="#d7dfd7" gap={24} size={1} />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                  <span className="canvas-caption">
                    拖动节点调整布局 · 点击查看配置
                  </span>
                </div>
                <div className="workflow-configuration">
                  <div className="configuration-title">
                    <Settings2 size={17} />
                    <h3>
                      {selectedNode === "chunk"
                        ? "文本分块"
                        : selectedNode === "extract"
                          ? "模型抽取配置"
                          : selectedNode === "review"
                            ? "审核设置"
                            : selectedNode === "validate"
                              ? "来源证据校验"
                              : "工作流配置"}
                    </h3>
                  </div>
                  <div className="workflow-config-grid">
                    <section>
                      <label className="studio-field">
                        <span>工作流名称</span>
                        <input
                          value={draft.name}
                          onChange={(e) =>
                            setDraft({ ...draft, name: e.target.value })
                          }
                        />
                      </label>
                      <SelectField
                        label="使用的 Skill 版本"
                        value={`${draft.skill_id}@${draft.skill_version}`}
                        onChange={(value) => {
                          const [id, version] = value.split("@");
                          setDraft({
                            ...draft,
                            skill_id: id,
                            skill_version: version,
                          });
                        }}
                      >
                        {skills.map((s) => (
                          <option
                            key={`${s.id}@${s.version}`}
                            value={`${s.id}@${s.version}`}
                          >
                            {s.name} · v{s.version}
                          </option>
                        ))}
                      </SelectField>
                      <div className="output-toggles">
                        {Object.entries(outputNames).map(([key, name]) => (
                          <label key={key}>
                            <span>{name}</span>
                            <input
                              type="checkbox"
                              role="switch"
                              checked={!!draft.nodes[key]}
                              onChange={(e) =>
                                setDraft({
                                  ...draft,
                                  nodes: {
                                    ...draft.nodes,
                                    [key]: e.target.checked,
                                  },
                                })
                              }
                            />
                          </label>
                        ))}
                      </div>
                    </section>
                    <section>
                      <div className="form-grid">
                        {[
                          ["chunk_chars", "每块字数"],
                          ["retry_limit", "额外重试次数"],
                          ["max_input_tokens", "单次输入上限"],
                          ["max_output_tokens", "单次输出上限"],
                          ["budget_tokens", "任务 token 预算"],
                        ].map(([key, label]) => (
                          <label className="studio-field" key={key}>
                            <span>{label}</span>
                            <input
                              type="number"
                              min={key === "retry_limit" ? 0 : 1}
                              value={Number(draft[key as keyof Workflow] || 0)}
                              onChange={(e) =>
                                setDraft({
                                  ...draft,
                                  [key]: Number(e.target.value),
                                })
                              }
                            />
                          </label>
                        ))}
                      </div>
                    </section>
                    <section className="prompt-section">
                      <label className="studio-field">
                        <span>自定义提示词</span>
                        <textarea
                          rows={6}
                          placeholder="例如：重点提取人物首次登场、伏笔及其回收；所有事实附原文证据。"
                          value={draft.custom_prompt}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              custom_prompt: e.target.value,
                            })
                          }
                        />
                      </label>
                    </section>
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      ) : (
        <div className="skills-layout">
          <aside className="surface skill-index">
            <div className="surface-heading">
              <h2>Skills</h2>
              <span>{skills.length}</span>
            </div>
            {loading ? (
              <Busy label="正在读取 Skills" />
            ) : (
              skills.map((s) => (
                <button
                  className={`workflow-list-item ${skill?.id === s.id && skill.version === s.version ? "active" : ""}`}
                  onClick={() => {
                    setSkill({ ...s, version: nextVersion(s.version) });
                    setSchema(describe(s.output_schema));
                  }}
                  key={`${s.id}@${s.version}`}
                >
                  <span>
                    <Code2 size={17} />
                    <strong>{s.name}</strong>
                  </span>
                  <small>
                    v{s.version}
                    {s.builtin ? " · 内置" : ""}
                  </small>
                </button>
              ))
            )}
          </aside>
          <section className="surface skill-editor">
            {!skill ? (
              <Empty
                icon={Code2}
                title="让你的分析习惯可以复用"
                description="上传或编写 Skill，定义提示词与结构化输出。"
              />
            ) : (
              <>
                <div className="surface-heading">
                  <h2>{skill.name}</h2>
                  <button
                    className="button primary"
                    disabled={!!busy}
                    onClick={() => void saveSkill()}
                  >
                    <Save size={15} />
                    保存 Skill 版本
                  </button>
                </div>
                <div className="skill-editor-body">
                  <div className="form-grid">
                    <label className="studio-field">
                      <span>Skill ID</span>
                      <input
                        value={skill.id}
                        onChange={(e) =>
                          setSkill({ ...skill, id: e.target.value })
                        }
                      />
                    </label>
                    <label className="studio-field">
                      <span>新版本</span>
                      <input
                        value={skill.version}
                        onChange={(e) =>
                          setSkill({ ...skill, version: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <label className="studio-field">
                    <span>名称</span>
                    <input
                      value={skill.name}
                      onChange={(e) =>
                        setSkill({ ...skill, name: e.target.value })
                      }
                    />
                  </label>
                  <label className="studio-field">
                    <span>说明</span>
                    <input
                      value={skill.description}
                      onChange={(e) =>
                        setSkill({ ...skill, description: e.target.value })
                      }
                    />
                  </label>
                  <label className="studio-field">
                    <span>提取提示词</span>
                    <textarea
                      rows={9}
                      value={skill.prompt}
                      onChange={(e) =>
                        setSkill({ ...skill, prompt: e.target.value })
                      }
                      placeholder="清楚描述需要提取的内容、约束和证据要求。"
                    />
                  </label>
                  <label className="studio-field">
                    <span>输出 JSON Schema</span>
                    <textarea
                      className="code-editor"
                      rows={12}
                      value={schema}
                      onChange={(e) => setSchema(e.target.value)}
                      spellCheck={false}
                    />
                  </label>
                </div>
              </>
            )}
          </section>
        </div>
      )}
      {saveOpen && draft && (
        <Modal
          title="保存工作流新版本"
          onClose={() => setSaveOpen(false)}
          busy={busy === "save"}
        >
          <div className="modal-body">
            <label className="studio-field">
              <span>工作流 ID</span>
              <input
                value={saveId}
                onChange={(e) => setSaveId(e.target.value)}
              />
            </label>
            <label className="studio-field">
              <span>版本</span>
              <input
                value={saveVersion}
                onChange={(e) => setSaveVersion(e.target.value)}
              />
            </label>
            <Notice error>{error}</Notice>
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setSaveOpen(false)}
            >
              取消
            </button>
            <button
              className="button primary"
              disabled={!!busy || !saveId || !saveVersion}
              onClick={() => void save()}
            >
              保存新版本
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
