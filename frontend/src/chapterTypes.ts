export type ChapterIssue = { kind: string; label: string; count?: number; severity?: string };
export type Chapter = { id: string; index: number; number?: number; title: string; display_label?: string; raw_marker?: string; kind?: string; placeholder?: boolean; gap_range?: boolean; missing_count?: number; start: number; end: number; body_start?: number; char_count: number; issues: ChapterIssue[]; proposal_count: number; proposal_ids?: string[]; enabled_proposal_ids?: string[] };
export type ChapterFormat = { name: string; format?: string; count?: number; score?: number; enabled?: boolean; custom?: boolean };
export type CustomFormat = { name: string; format?: string; regex?: string; enabled: boolean };
export type ChapterIndex = { version_id: string; pattern?: string; heading_formats?: string[]; candidates?: ChapterFormat[]; gaps?: { missing_numbers?: number[]; after_number?: number; before_number?: number; missing_count?: number }[]; chapters: Chapter[]; options?: { author_keywords?: string[]; garble_rules?: { name: string; pattern: string; enabled: boolean }[]; custom_formats?: CustomFormat[] }; author_keyword_defaults?: string[] };
export type Proposal = { id: string; start: number; end: number; text: string; kind: string; label: string; reason: string; enabled: boolean; removable: boolean; context_before?: string; context_after?: string };
export type Finding = Proposal & { chapter_id: string; chapter_index: number; chapter_title: string };
export type ChapterPage = Chapter & { version_id?: string; chapter?: Chapter; text: string; start: number; end: number; total: number; body_offset: number; body_total?: number; body_text?: string | null; editable_start?: number; editable_end?: number; editable_text?: string; expected_text?: string; source_offset_map?: number[]; next_start?: number | null; proposals: Proposal[] };
export const issueClass = (kind: string) => /glyph|garbl|mojibake|replacement/.test(kind) ? "glyph" : /author|aside|nonbody|non_body/.test(kind) ? "aside" : /length|gap|duplicate|outlier/.test(kind) ? "length" : "noise";
export function chapterLabel(chapter: Chapter) {
  if (chapter.display_label) return chapter.display_label;
  if (chapter.kind === "preamble") return chapter.title || "前言";
  const title = chapter.title.replace(/^第[\d零〇一二三四五六七八九十百千万两]+[章回节]\s*/u, "").trim();
  return `第${chapter.number ?? chapter.index + 1}章${title ? ` ${title}` : ""}`;
}
export const needsReview = (chapter: Chapter) => !!chapter.placeholder || chapter.kind === "missing" || !!chapter.issues?.length || chapter.proposal_count > 0;
