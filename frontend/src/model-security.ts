type ModelTarget = { connection_mode?: "local" | "remote"; base_url?: string };

export function isLoopbackEndpoint(value: string) {
  try {
    const host = new URL(value).hostname.toLowerCase();
    return ["localhost", "[::1]", "::1"].includes(host) || /^127(?:\.\d{1,3}){3}$/.test(host);
  } catch { return false; }
}

export function isRemoteModel(target?: ModelTarget) {
  if (!target) return false;
  return target.connection_mode
    ? target.connection_mode === "remote"
    : !isLoopbackEndpoint(target.base_url || "");
}

export function modelEndpoint(value?: string, includePath = false) {
  if (!value) return "尚未配置";
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.host}${includePath ? url.pathname.replace(/\/$/, "") : ""}`;
  } catch { return "请输入有效的服务地址"; }
}
