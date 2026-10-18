import type { EvidenceItem } from "./studio";

export type ReferenceRecord = Record<string, unknown>;
export type ReferenceSection = "plot" | "characters" | "settings" | "style" | "rhythm" | "opening" | "setting_features" | "raw_entries" | "metadata";
export type ReferenceChunkRange = { key: string; title: string; startChapter: number; endChapter: number };
export type ReferenceState = {
  document_id: string;
  revision: number;
  structure_type: "narrative" | "setting_collection";
  sections: Record<ReferenceSection, unknown>;
  commits: { run_id: string; chunk_id: string; section: string; committed_at: string; source_version_id?: string }[];
};
export type ReferenceResult = {
  summary?: string;
  characters?: EvidenceItem[];
  events?: EvidenceItem[];
  cliches?: EvidenceItem[];
  reference?: Partial<Record<ReferenceSection, unknown>>;
  [key: string]: unknown;
};
export const record = (value: unknown): ReferenceRecord => value && typeof value === "object" && !Array.isArray(value) ? value as ReferenceRecord : {};
export const records = (value: unknown): ReferenceRecord[] => Array.isArray(value) ? value.filter(v => v && typeof v === "object" && !Array.isArray(v)) : [];
export const stringValue = (value: unknown): string => typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
export const stringList = (value: unknown): string[] => Array.isArray(value) ? value.map(stringValue).filter(Boolean) : typeof value === "string" ? value.split(/\n/).filter(Boolean) : [];
export const settingCategories = [
  ["power_system", "力量体系"], ["factions", "势力组织"], ["geography", "地理"],
  ["social_rules", "社会规则"], ["history", "历史背景"], ["worldview", "世界观"], ["other", "其他"],
];
export const chapterTypes = ["日常", "战斗", "高潮", "角色个人回", "主线事件", "支线事件", "伏笔铺垫", "收束", "转折", "其他"];
export const roleTags = ["主角", "女主角", "反派", "男配", "女配", "师长", "重要配角", "路人", "其他"];
export function sectionCount(state: ReferenceState | null, section: ReferenceSection) {
  const data = state?.sections[section];
  if (section === "plot") return records(record(data).epochs).reduce((n, e) => n + records(e.periods).reduce((sum, p) => sum + records(p.events).length, 0), 0);
  return Array.isArray(data) ? data.length : Object.keys(record(data)).length;
}
export function chapterNumber(value: unknown): number | null {
  const s = stringValue(value);
  const arabic = s.match(/(?:第\s*)?(\d+)\s*(?:章|回|节|话)?/);
  if (arabic) return Number(arabic[1]);
  const chinese = s.match(/第\s*([零〇一二两三四五六七八九十百千万]+)\s*[章回节话]/)?.[1];
  if (!chinese) return null;
  const digits: Record<string, number> = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  const units: Record<string, number> = { 十: 10, 百: 100, 千: 1000, 万: 10000 };
  let total = 0, section = 0, digit = 0;
  for (const char of chinese) { if (char in digits) digit = digits[char]; else if (units[char] === 10000) { total += (section + digit) * 10000; section = 0; digit = 0; } else { section += (digit || 1) * units[char]; digit = 0; } }
  return total + section + digit;
}
export function referenceChunkLabel(chapter: unknown, ranges: ReferenceChunkRange[]) {
  const number = chapterNumber(chapter);
  if (number === null) return "未标章节";
  return ranges.find(c => number >= c.startChapter && number <= c.endChapter)?.title || `第 ${number} 章 · 未分段`;
}
