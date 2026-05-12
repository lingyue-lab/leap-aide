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
}

export interface SessionListItem {
  session_id: string;
  title: string;
  created_at: number;
  updated_at?: number;
  pinned?: boolean;
  workspace?: string;
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

export interface Settings {
  model: string;
  send_key: SendKey;
  theme: Theme;
  language: string;
  sound_enabled: boolean;
  notifications_enabled: boolean;
  show_token_usage: boolean;
  bubble_layout: boolean;
  show_cli_sessions: boolean;
  sync_insights: boolean;
  check_updates: boolean;
  bot_name: string;
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

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  estimated_cost?: number | null;
  context_length?: number;
  last_prompt_tokens?: number;
  threshold_tokens?: number;
}

// types/nav.ts - 导航相关类型定义

export type NavPanel = 'chat' | 'tasks' | 'skills' | 'memory' | 'workspaces' | 'profiles' | 'todos';

export interface NavTab {
  panel: NavPanel;
  label: string;
  titleKey: string;
  icon: React.ReactNode;
}

// types/cron.ts - 定时任务相关类型定义

export interface CronJob {
  id: string;
  name?: string;
  schedule: string;
  prompt: string;
  deliver: 'local' | 'discord' | 'telegram';
  skills?: string[];
  enabled: boolean;
  created_at?: number;
  last_run?: number;
  last_output?: string;
  error?: string;
}

export interface CronCreateRequest {
  name?: string;
  schedule: string;
  prompt: string;
  deliver: 'local' | 'discord' | 'telegram';
  skills?: string[];
}

// types/skill.ts - 技能相关类型定义

export interface Skill {
  name: string;
  label: string;
  description?: string;
  category?: string;
  content?: string;
  created_at?: number;
  updated_at?: number;
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

// types/profile.ts - 配置相关类型定义

export interface Profile {
  name: string;
  path: string;
  is_default: boolean;
  is_active: boolean;
  gateway_running: boolean;
  model?: string;
  provider?: string;
  has_env: boolean;
  skill_count: number;
}
