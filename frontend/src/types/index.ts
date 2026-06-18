// types/session.ts - 会话相关类型定义

export interface Session {
  session_id: string;
  title: string;
  workspace?: string;
  model?: string;
  messages: Message[];
  created_at: number;
  updated_at?: number;
  tool_calls?: ToolCall[];
  active_stream_id?: string | null;
  pending_user_message?: string | null;
  pending_attachments?: string[];
  pinned?: boolean;
  input_tokens?: number;
  output_tokens?: number;
  estimated_cost?: number | null;
  skills?: string[] | null;
  message_count?: number;
  last_seen_msg_count?: number | null;
  // Compression lineage (v1 — WebUI-native sessions only)
  _parent_session_id?: string | null;
  _compression_segment_count?: number;
  _lineage_root_id?: string | null;
  _lineage_tip_id?: string | null;
  compression_anchor_visible_idx?: number | null;
  compression_anchor_message_key?: string | null;
  _readonly?: boolean;
  // Persistent compressor data — restores ContextUsageIndicator after page refresh
  context_length?: number;
  last_prompt_tokens?: number;
  threshold_tokens?: number;
  last_compression_time?: number;  // epoch seconds — set by backend on compression
}

export interface SessionListItem {
  session_id: string;
  title: string;
  created_at: number;
  updated_at?: number;
  pinned?: boolean;
  workspace?: string;
  skills?: string[] | null;
  message_count?: number;
  last_seen_msg_count?: number | null;
  // Compression chain metadata (v1 — WebUI-native sessions only)
  _compression_segment_count?: number;
  _lineage_root_id?: string | null;
  _lineage_tip_id?: string | null;
  _parent_session_id?: string | null;
  _readonly?: boolean;
  _compaction_n?: number;
}

// types/message.ts - 消息相关类型定义

export interface Message {
  role: 'user' | 'assistant' | 'tool';
  content: string | ContentBlock[];
  timestamp?: number;
  _ts?: number;
  _live?: boolean;
  attachments?: string[];
  reasoning?: string;
  tool_call_id?: string;
  tool_name?: string;
  tool_calls?: ToolCallMessage[];
  _compression?: CompressionRecord;  // Attached by streamManager on 'compressed' SSE event
}

export interface ToolCallMessage {
  id?: string;
  call_id?: string;
  type?: string;
  function?: {
    name?: string;
    arguments?: string | Record<string, unknown>;
  };
}

export interface ContentBlock {
  type: 'text' | 'image' | 'tool_use' | 'tool_result' | 'thinking' | 'reasoning';
  text?: string;
  thinking?: string;
  reasoning?: string;
  source?: { type: string; media_type: string; data: string };
  name?: string;
  input?: Record<string, unknown>;
  content?: string;
  id?: string;  // tool_call_id for tool_use blocks
}

export interface ToolCall {
  name: string;
  preview: string;
  args: Record<string, unknown>;
  snippet: string;
  done: boolean;
  tid: string;
  is_error?: boolean;
  duration?: number;
}

// types/workspace.ts - 工作区相关类型定义

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  modified?: string;
}

export interface Workspace {
  name: string;
  path: string;
  display_name?: string;
}

export interface DirEntry {
  entries: FileNode[];
  path: string;
  git?: { branch?: string; modified?: number };
}

// types/settings.ts - 设置相关类型定义

export type SendKey = 'enter' | 'ctrl+enter';
export type Theme = 'system' | 'dark' | 'light' | 'slate' | 'solarized' | 'monokai' | 'nord' | 'oled';
export type FontSize = 'small' | 'medium' | 'large';

export interface Settings {
  model: string;
  send_key: SendKey;
  theme: Theme;
  language: string;
  font_size: FontSize;
  sound_enabled: boolean;
  notifications_enabled: boolean;
  show_token_usage: boolean;
  show_cli_sessions: boolean;
  sync_insights: boolean;
  check_updates: boolean;
  bot_name: string;
  auto_compress_threshold?: string;       // Path 1 — auto-compress by message count
  auto_compress_token_threshold?: string;  // Path 3 — auto-compress by token % of context
  max_compaction_snapshots?: string;       // max archived compaction snapshots to retain
  last_seen_cron_ts?: number;  // DEPRECATED — replaced by last_seen_cron_ts_map
  last_seen_cron_ts_map?: Record<string, number>;  // per-job {job_id: epoch_seconds}
  default_mode?: 'manual' | 'smart' | 'yolo';
}

// types/approval.ts - 审批相关类型定义

export interface ApprovalRequest {
  approval_id?: string;
  command?: string;
  description?: string;
  pattern_keys?: string[];
  pattern_key?: string;
  _session_id?: string;
}

export interface ApprovalResponse {
  session_id: string;
  choice: 'once' | 'session' | 'always' | 'deny';
  approval_id?: string | null;
}

export interface SessionApprovedResponse {
  session_approved: string[];
  permanent_approved: string[];
}

export interface RevokeRequest {
  session_id: string;
  pattern_key: string;
}

// types/clarify.ts - 澄清问题相关类型定义

export interface ClarifyRequest {
  question?: string;
  description?: string;
  choices_offered?: string[];
  choices?: string[];
  _session_id?: string;
}

export interface ClarifyResponse {
  session_id: string;
  response: string;
}

// types/api.ts - API 响应类型定义

export interface ApiResponse<T> {
  ok: boolean;
  message?: string;
  data?: T;
}

export interface StreamStatusResponse {
  active: boolean;
  session_id?: string;
}

export interface ModelsResponse {
  groups: ModelGroup[];
  default_model?: string;
  active_provider?: string;
}

export interface ModelGroup {
  provider: string;
  models: Model[];
}

export interface Model {
  id: string;
  label: string;
}

export interface Provider {
  id: string;
  display_name: string;
  has_key: boolean;
  configurable: boolean;
  is_oauth: boolean;
  key_source: 'env_file' | 'env_var' | 'config_yaml' | 'oauth' | 'none';
  auth_error?: string | null;
  models: Array<{ id: string; label: string }>;
}

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  estimated_cost?: number | null;
  context_length?: number;
  last_prompt_tokens?: number;
  threshold_tokens?: number;
}

// ============================================================
// Compression-related types
// ============================================================

/** Represents a single compression event in a chat session. */
export interface CompressionRecord {
  compression_time: number;
  messages_before: number;
  messages_after: number;
  approx_tokens_before: number;
  approx_tokens_after: number;
  summary_text?: string | null;
  trigger: 'auto' | 'manual';
}

/** Represents one segment in a compression chain (for sidebar display). */
export interface CompressionChainSegment {
  session_id: string;
  title: string;
  message_count: number;
  created_at: number;
  is_active: boolean;
  parent_session_id?: string | null;
}

// types/nav.ts - 导航相关类型定义

export type NavPanel = 'chat' | 'tasks' | 'skills' | 'memory' | 'workspaces' | 'terminal' | 'vnc' | 'settings';

export interface NavTab {
  panel: NavPanel;
  label: string;
  titleKey: string;
  icon: React.ReactNode;
}

// types/cron.ts - 定时任务相关类型定义

export interface CronSchedule {
  kind: string;
  minutes: number;
  display: string;
}

export interface CronJob {
  id: string;
  name?: string;
  schedule: string | CronSchedule;
  prompt: string;
  deliver: 'local' | 'discord' | 'telegram';
  skills?: string[];
  enabled: boolean;
  created_at?: number;
  last_run?: number;
  last_run_at?: string | number;
  next_run_at?: string | number | null;
  last_output?: string;
  last_status?: string;
  error?: string;
}

export interface CronCreateRequest {
  name?: string;
  schedule: string;
  prompt: string;
  deliver: 'local' | 'discord' | 'telegram';
  skills?: string[];
}

export interface CronCompletion {
  job_id: string;
  name: string;
  status: string;
  completed_at: number;
  has_new?: boolean;
}

// types/skill.ts - 技能相关类型定义

export interface Skill {
  name: string;
  dir_name?: string;
  label: string;
  description?: string;
  category?: string;
  content?: string;
  created_at?: number;
  updated_at?: number;
}

export interface SkillFileNode {
  name: string;
  path: string;    // 相对于 skill 目录的路径
  is_dir: boolean;
  size?: number;
  children?: SkillFileNode[];
}

// types/memory.ts - 记忆相关类型定义

export interface MemorySection {
  section: 'memory' | 'project';
  content: string;
  updated_at?: number;
}

export interface MemoryData {
  user_memory?: MemorySection;
  project_memory?: MemorySection;
}
