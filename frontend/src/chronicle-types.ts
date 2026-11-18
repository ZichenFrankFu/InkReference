export const eventTypes = ["推进", "日常", "压抑", "高潮", "过渡"] as const;
export const settingTypes = ["世界观", "地点", "超能力", "器物", "组织"] as const;
export const clueStatuses = ["已收回", "部分收回", "未收回"] as const;

export type ChronicleEvent = {
  id: string;
  title: string;
  type: typeof eventTypes[number];
  story_time: string;
  location: string;
  description: string;
  storyline_ids: string[];
  basis: string;
  source_lines: number[];
};
export type ChronicleCharacter = {
  id: string;
  name: string;
  aliases: string[];
  identity: string;
  basis: string;
  source_lines: number[];
};
export type ChronicleRelationship = {
  character_a_id: string;
  character_b_id: string;
  narrative_order: number;
  end_order?: number | null;
  relation: string;
  basis: string;
  source_lines: number[];
};
export type ChronicleSetting = {
  id: string;
  name: string;
  category: typeof settingTypes[number];
  description: string;
  source_lines: number[];
};
export type ChronicleStoryline = {
  id: string;
  title: string;
  character_ids: string[];
  beginning: string;
  development: string;
  turn: string;
  conclusion: string;
};
export type ClueEndpoint = {
  basis: string;
  source_lines: number[];
  event_id: string | null;
};
export type ChronicleClue = {
  id: string;
  title: string;
  status: typeof clueStatuses[number];
  plant: ClueEndpoint;
  return: ClueEndpoint | null;
  storyline_id: string | null;
};
export type ChronicleQuestion = {
  id: string;
  question: string;
  options: { id: "A" | "B"; label: string; effect: string }[];
  source_lines: number[];
};
export type AnswerChoice = "A" | "B" | "custom" | "defer";
export type AnswerScope = "item" | "book" | "similar";
export type Answer = { choice: AnswerChoice; note: string; scope: AnswerScope };
export type ChronicleDecision = Answer & { question_id: string; question: string };
export type ChronicleResult = {
  events: ChronicleEvent[];
  storylines: ChronicleStoryline[];
  characters: ChronicleCharacter[];
  relationships: ChronicleRelationship[];
  settings: ChronicleSetting[];
  foreshadowing: ChronicleClue[];
  questions: ChronicleQuestion[];
};
export type ChronicleRun = {
  id: string;
  version_id: string;
  status: string;
  stage: string;
  current: number;
  total: number;
  error?: string;
};
export type ChronicleState = {
  revision: number;
  version_id: string;
  result: ChronicleResult | null;
  run: ChronicleRun | null;
  decisions?: ChronicleDecision[];
  model?: { profile_id: string; name: string; model: string; base_url: string; connection_mode: "local" | "remote"; target_fingerprint?: string } | null;
};
export type SourceLine = { line: number; text: string; chapter?: string };
export type ViewSource = (lines: number[], title: string) => void;
export const formatNarrativeOrder = (index: number) => `E${String(index + 1).padStart(3, "0")}`;
