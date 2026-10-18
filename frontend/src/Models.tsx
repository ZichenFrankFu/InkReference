import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  Cloud,
  Cpu,
  KeyRound,
  Plus,
  Save,
  Server,
  ShieldCheck,
  Trash2,
  Wallet,
} from "lucide-react";
import {
  studio,
  send,
  normalizeList,
  type ModelProfile,
  type List,
} from "./studio";
import {
  Busy,
  Empty,
  Metric,
  Modal,
  Notice,
  PageTitle,
  SelectField,
  dateLabel,
  messageOf,
} from "./ui";
const tokenSource = (value: string | undefined) =>
  ({ actual: "实测", estimated: "估算", unknown: "未知", mixed: "混合" })[
    value || "unknown"
  ] || value;
const blank: ModelProfile = {
  id: "",
  name: "",
  provider: "ollama",
  base_url: "http://127.0.0.1:11434",
  model: "",
  context_window: 32768,
  max_output_tokens: 3000,
  timeout_seconds: 180,
  key_env: "",
  api_key: "",
  input_price_per_million: null,
  output_price_per_million: null,
  currency: "USD",
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
  const [draft, setDraft] = useState<ModelProfile | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [usage, setUsage] = useState<Usage[]>([]);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    message: string;
    model?: string;
  } | null>(null);
  const load = useCallback(async () => {
    try {
      setItems(normalizeList(await studio<List<ModelProfile>>("/models")));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }, []);
  const loadUsage = useCallback(async () => {
    setBusy("usage");
    try {
      const result = await studio<{ items: Usage[]; summary: UsageSummary }>(
        `/usage${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
      );
      setUsage(result.items);
      setSummary(result.summary);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  }, [runId]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (tab === "usage") void loadUsage();
  }, [tab]);
  const save = async () => {
    if (!draft) return;
    setBusy("save");
    setError("");
    setMessage("");
    try {
      const payload = {
        ...draft,
        id: draft.id || `model-${Date.now().toString(36)}`,
      };
      delete payload.has_session_key;
      delete payload.key_available;
      if (!payload.api_key) delete payload.api_key;
      const result = await send<ModelProfile>(
        isNew ? "/models" : `/models/${draft.id}`,
        payload,
        isNew ? "POST" : "PUT",
      );
      setDraft({ ...result, api_key: "" });
      setIsNew(false);
      setMessage("模型配置已保存。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const test = async () => {
    if (!draft) return;
    setBusy("test");
    setError("");
    setTestResult(null);
    try {
      const result = await send<{
        ok: boolean;
        message: string;
        model?: string;
      }>(`/models/${draft.id}/test`, {});
      setTestResult(result);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const remove = async () => {
    if (!draft) return;
    setBusy("delete");
    setError("");
    try {
      await studio(`/models/${draft.id}`, { method: "DELETE" });
      setDraft(null);
      setConfirmDelete(false);
      setMessage("模型配置已删除。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const update = <K extends keyof ModelProfile>(
    key: K,
    value: ModelProfile[K],
  ) => setDraft((d) => (d ? { ...d, [key]: value } : d));
  const number = (n?: number) => Number(n || 0).toLocaleString();
  return (
    <main className="studio-main">
      <PageTitle
        kicker="MODEL CONNECTIONS"
        title="模型与用量"
        description="选择本地或云端模型，看清每一次调用。"
        action={
          <button
            className="button primary"
            onClick={() => {
              setDraft({ ...blank });
              setIsNew(true);
              setTestResult(null);
              setTab("models");
              setError("");
            }}
          >
            <Plus size={17} />
            添加模型
          </button>
        }
      />
      <div className="page-tabs">
        <button
          className={tab === "models" ? "active" : ""}
          onClick={() => setTab("models")}
        >
          <Server size={16} />
          模型配置<span>{items.length}</span>
        </button>
        <button
          className={tab === "usage" ? "active" : ""}
          onClick={() => setTab("usage")}
        >
          <Activity size={16} />
          调用与用量
        </button>
      </div>
      <Notice error={!!error} success={!error && !!message}>
        {error || message}
      </Notice>
      {tab === "models" ? (
        <div className="models-layout">
          <aside className="model-cards">
            {loading ? (
              <Busy label="正在读取模型配置" />
            ) : !items.length ? (
              <Empty
                icon={Cpu}
                title="连接第一个模型"
                description="支持本地 Ollama 和兼容 OpenAI 的 API。"
              />
            ) : (
              items.map((p) => (
                <button
                  className={`surface model-card ${draft?.id === p.id ? "active" : ""}`}
                  key={p.id}
                  onClick={() => {
                    setDraft({ ...p, api_key: "" });
                    setIsNew(false);
                    setTestResult(null);
                    setError("");
                  }}
                >
                  <span
                    className={`model-avatar ${p.provider === "ollama" ? "local" : "cloud"}`}
                  >
                    {p.provider === "ollama" ? (
                      <Cpu size={24} />
                    ) : (
                      <Cloud size={24} />
                    )}
                  </span>
                  <span className="model-card-text">
                    <strong>{p.name}</strong>
                    <span>{p.model}</span>
                    <small>
                      {p.provider === "ollama"
                        ? "本地 · Ollama"
                        : "API · OpenAI compatible"}
                    </small>
                  </span>
                  <span className="model-key-status">
                    {p.provider === "ollama" ? (
                      <span className="local-dot" />
                    ) : p.key_available ? (
                      <ShieldCheck size={15} />
                    ) : (
                      <KeyRound size={15} />
                    )}
                  </span>
                </button>
              ))
            )}
          </aside>
          <section className="surface model-editor">
            {!draft ? (
              <Empty
                icon={Server}
                title="为分析选择合适的模型"
                description="选择已有配置，或添加一个新连接。"
              />
            ) : (
              <>
                <div className="surface-heading">
                  <h2>{isNew ? "添加模型" : draft.name}</h2>
                  {!isNew && (
                    <button
                      className="icon-button danger"
                      aria-label="删除模型配置"
                      onClick={() => setConfirmDelete(true)}
                    >
                      <Trash2 size={17} />
                    </button>
                  )}
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void save();
                  }}
                >
                  <div className="model-editor-body">
                    <div className="form-grid">
                      <label className="studio-field">
                        <span>配置名称</span>
                        <input
                          required
                          value={draft.name}
                          placeholder="例如：本地 Qwen"
                          onChange={(e) => update("name", e.target.value)}
                        />
                      </label>
                      <SelectField
                        label="连接方式"
                        value={draft.provider}
                        onChange={(value) => {
                          update("provider", value as ModelProfile["provider"]);
                          update(
                            "base_url",
                            value === "ollama"
                              ? "http://127.0.0.1:11434"
                              : "https://api.openai.com/v1",
                          );
                        }}
                      >
                        <option value="ollama">本地 Ollama</option>
                        <option value="openai_compatible">
                          OpenAI compatible API
                        </option>
                      </SelectField>
                    </div>
                    <label className="studio-field">
                      <span>服务地址</span>
                      <input
                        required
                        value={draft.base_url}
                        onChange={(e) => update("base_url", e.target.value)}
                        spellCheck={false}
                      />
                    </label>
                    <label className="studio-field">
                      <span>模型名称</span>
                      <input
                        required
                        value={draft.model}
                        placeholder={
                          draft.provider === "ollama"
                            ? "qwen3:8b"
                            : "服务商提供的模型 ID"
                        }
                        onChange={(e) => update("model", e.target.value)}
                        spellCheck={false}
                      />
                    </label>
                    {draft.provider === "openai_compatible" && (
                      <div className="credential-panel">
                        <h3>
                          <KeyRound size={15} />
                          调用凭据
                        </h3>
                        <label className="studio-field">
                          <span>
                            本次会话 API Key
                            {draft.has_session_key && (
                              <span className="key-badge">已设置</span>
                            )}
                          </span>
                          <input
                            type="password"
                            value={draft.api_key || ""}
                            onChange={(e) => update("api_key", e.target.value)}
                            autoComplete="new-password"
                            placeholder={
                              draft.has_session_key
                                ? "输入以替换本次会话凭据"
                                : "仅在当前服务进程内使用"
                            }
                          />
                        </label>
                        <label className="studio-field">
                          <span>或读取环境变量</span>
                          <input
                            value={draft.key_env || ""}
                            placeholder="例如 OPENAI_API_KEY"
                            onChange={(e) => update("key_env", e.target.value)}
                            spellCheck={false}
                          />
                        </label>
                      </div>
                    )}
                    <div className="form-grid">
                      {[
                        ["context_window", "上下文窗口"],
                        ["max_output_tokens", "最大输出 token"],
                        ["timeout_seconds", "请求超时（秒）"],
                      ].map(([key, label]) => (
                        <label className="studio-field" key={key}>
                          <span>{label}</span>
                          <input
                            type="number"
                            min={1}
                            required
                            value={Number(draft[key as keyof ModelProfile])}
                            onChange={(e) =>
                              update(
                                key as "context_window",
                                Number(e.target.value),
                              )
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <details className="pricing-details">
                      <summary>
                        <Wallet size={15} />
                        费用估算价格<span>可选</span>
                      </summary>
                      <div className="form-grid">
                        <label className="studio-field">
                          <span>每百万输入 token</span>
                          <input
                            type="number"
                            min={0}
                            step="any"
                            value={draft.input_price_per_million ?? ""}
                            placeholder="未配置"
                            onChange={(e) =>
                              update(
                                "input_price_per_million",
                                e.target.value === ""
                                  ? null
                                  : Number(e.target.value),
                              )
                            }
                          />
                        </label>
                        <label className="studio-field">
                          <span>每百万输出 token</span>
                          <input
                            type="number"
                            min={0}
                            step="any"
                            value={draft.output_price_per_million ?? ""}
                            placeholder="未配置"
                            onChange={(e) =>
                              update(
                                "output_price_per_million",
                                e.target.value === ""
                                  ? null
                                  : Number(e.target.value),
                              )
                            }
                          />
                        </label>
                        <label className="studio-field">
                          <span>货币</span>
                          <input
                            value={draft.currency}
                            onChange={(e) => update("currency", e.target.value)}
                          />
                        </label>
                      </div>
                    </details>
                    {testResult && (
                      <Notice success={testResult.ok} error={!testResult.ok}>
                        {testResult.message ||
                          `${testResult.model || draft.model} 连接成功`}
                      </Notice>
                    )}
                    {busy && (
                      <Busy
                        label={
                          {
                            save: "正在保存模型配置",
                            test: "正在发起真实模型调用",
                            delete: "正在删除配置",
                          }[busy]
                        }
                      />
                    )}
                  </div>
                  <footer className="model-footer">
                    <span>
                      {draft.provider === "ollama" ? (
                        <>
                          <Cpu size={13} />
                          本地推理
                        </>
                      ) : (
                        <>
                          <KeyRound size={13} />
                          {draft.key_available ? "凭据可用" : "请设置调用凭据"}
                        </>
                      )}
                    </span>
                    <div>
                      {!isNew && (
                        <button
                          className="button secondary"
                          type="button"
                          disabled={!!busy}
                          onClick={() => void test()}
                        >
                          测试已保存配置
                          <ArrowRight size={14} />
                        </button>
                      )}
                      <button
                        className="button primary"
                        disabled={!!busy}
                        type="submit"
                      >
                        <Save size={15} />
                        保存配置
                      </button>
                    </div>
                  </footer>
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
      {confirmDelete && (
        <Modal
          title="删除模型配置"
          onClose={() => setConfirmDelete(false)}
          busy={busy === "delete"}
        >
          <div className="modal-body">
            <p>删除“{draft?.name}”的连接配置，历史调用记录保留。</p>
            <Notice error>{error}</Notice>
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setConfirmDelete(false)}
            >
              取消
            </button>
            <button
              className="button destructive"
              disabled={!!busy}
              onClick={() => void remove()}
            >
              确认删除
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
