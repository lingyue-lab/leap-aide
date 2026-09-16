import type { CSSProperties, MouseEventHandler } from 'react';
import {
  // Layout & navigation
  GitFork,
  Plus,
  ArrowUp,
  ArrowDown,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Menu,
  MoreHorizontal,
  MoreVertical,
  // Files & folders
  Paperclip,
  Copy,
  FolderPlus,
  Trash2,
  Trash,
  FileText,
  PenLine,
  Pencil,
  Folder,
  FolderOpen,
  FilePlus,
  Upload,
  // Status & feedback
  Zap,
  XCircle,
  Check,
  Square,
  Circle,
  X,
  Info,
  Shield,
  Star,
  Eye,
  AlertTriangle,
  // Lists & tasks
  ListChecks,
  List,
  History,
  // Time & calendar
  Calendar,
  // Settings & system
  Puzzle,
  Sun,
  Lock,
  LogOut,
  Monitor,
  Save,
  Settings,
  Bot,
  Terminal,
  // Chat & people
  MessageCircle,
  Mic,
  User,
  Send,
  // Common actions
  Minimize2,
  RefreshCw,
  Search,
  // Marker / state
  Pin,
  Layers,
  Lightbulb,
  // Misc / fallback set
  HelpCircle,
  Bug,
  Rocket,
  Mail,
  Phone,
  Unlock,
  Minus,
  Code,
} from 'lucide-react';

export interface IconProps {
  size?: number;
  className?: string;
}

export interface MaterialIconProps extends IconProps {
  /** Icon name (snake_case), e.g. "close", "folder_open". Mapped to a lucide-react component. */
  name: string;
  /** 0 = outlined, 1 = filled. lucide-react uses `fill="currentColor"` for filled state. */
  fill?: 0 | 1;
  title?: string;
  onClick?: MouseEventHandler<HTMLSpanElement>;
  style?: CSSProperties;
}

/**
 * Mapping from historical Material Symbols ligature names to lucide-react icons.
 *
 * Why this map exists: the project used Material Symbols (a 5MB variable font) for
 * icons. lucide-react SVG icons replace that font. We preserve the `<Icon name="X" />`
 * API so the ~95 call sites across 30+ files don't need to change.
 *
 * To add a new icon:
 *   1. Find the matching lucide-react component (https://lucide.dev).
 *   2. Add an entry to KNOWN_ICONS below. The fallback is HelpCircle.
 *   3. In dev mode, a console.warn fires for unknown names so we notice gaps early.
 */
const KNOWN_ICONS: Record<string, React.ComponentType<MaterialGlyphProps>> = {
  // Layout & navigation
  account_tree: GitFork,
  add: Plus,
  arrow_upward: ArrowUp,
  arrow_up: ArrowUp,
  arrow_down: ArrowDown,
  arrow_forward: ArrowRight,
  arrow_right: ArrowRight,
  chevron_left: ChevronLeft,
  chevron_right: ChevronRight,
  expand_more: ChevronDown,
  keyboard_arrow_down: ChevronDown,
  menu: Menu,
  more_horiz: MoreHorizontal,
  more_vert: MoreVertical,

  // Files & folders
  attach_file: Paperclip,
  content_copy: Copy,
  create_new_folder: FolderPlus,
  delete: Trash2,
  description: FileText,
  drive_file_rename: PenLine,
  edit: Pencil,
  folder: Folder,
  folder_open: FolderOpen,
  note_add: FilePlus,
  upload: Upload,

  // Status & feedback
  bolt: Zap,
  cancel: XCircle,
  check: Check,
  check_box_outline_blank: Square,
  circle: Circle,
  close: X,
  info: Info,
  shield: Shield,
  star: Star,
  visibility: Eye,
  warning: AlertTriangle,

  // Lists & tasks
  checklist: ListChecks,
  format_list_bulleted: List,
  history: History,

  // Time & calendar
  calendar_month: Calendar,

  // Settings & system
  extension: Puzzle,
  light_mode: Sun,
  lock: Lock,
  logout: LogOut,
  monitor: Monitor,
  save: Save,
  settings: Settings,
  smart_toy: Bot,
  support_agent: MaterialSupportAgent,
  terminal_2: Terminal,

  // Chat & people
  chat_bubble: MessageCircle,
  mic: Mic,
  person: User,
  psychology: MaterialPsychology,
  send: Send,

  // Common actions
  compress: Minimize2,
  refresh: RefreshCw,
  search: Search,

  // Marker / state
  keep: Pin,
  layers: Layers,
  lightbulb: Lightbulb,

  // Misc fallbacks for future LLM-injected names
  help: HelpCircle,
  robot: Bot,
  bug: Bug,
  rocket: Rocket,
  mail: Mail,
  phone: Phone,
  unlock: Unlock,
  plus: Plus,
  minus: Minus,
  trash: Trash,
  code: Code,
  stop: Square,
};

const ICON_FALLBACK = HelpCircle;

// ── Inline Material Symbols Rounded SVGs ─────────────────────────────
// These two panel-tab icons (`support_agent`, `psychology`) keep their
// original Material Symbols Rounded look — they are large, prominent icons
// that define the identity of the Chat and Memory tabs.
//
// Path data was extracted from material-symbols-rounded.woff2 and
// pre-transformed so the SVG matches lucide-react's 0-24 viewBox:
//   • Scaled from 960 → 24 units (divide all coords by 40)
//   • Y-flip applied (font has Y growing up; SVG has Y growing down)
// No runtime `transform` attribute is needed, so positioning is identical
// to every other lucide icon.

interface MaterialGlyphProps {
  size?: number | string;
  style?: CSSProperties;
}

/**
 * Material Symbols Rounded "support_agent" — a customer-service agent with a headset.
 */
function MaterialSupportAgent({ size = 18, style }: MaterialGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      fillRule="evenodd"
      style={style}
      aria-hidden="true"
    >
      <path d="M12 21Q11.575 21 11.2875 20.7125Q11 20.425 11 20Q11 19.575 11.2875 19.2875Q11.575 19 12 19H19Q19 19 19 19Q19 19 19 19V11.9Q19 8.975 16.9625 6.9375Q14.925 4.9 12 4.9Q9.075 4.9 7.0375 6.9375Q5 8.975 5 11.9V17Q5 17.425 4.7125 17.7125Q4.425 18 4 18Q3.175 18 2.5875 17.4125Q2 16.825 2 16V14Q2 13.475 2.2625 13.0125Q2.525 12.55 3 12.275L3.075 10.95Q3.275 9.25 4.0625 7.8Q4.85 6.35 6.0375 5.275Q7.225 4.2 8.7625 3.6Q10.3 3 12 3Q13.7 3 15.225 3.6Q16.75 4.2 17.95 5.2625Q19.15 6.325 19.925 7.775Q20.7 9.225 20.925 10.925L21 12.225Q21.475 12.45 21.7375 12.9Q22 13.35 22 13.85V16.15Q22 16.65 21.7375 17.1Q21.475 17.55 21 17.775V19Q21 19.825 20.4125 20.4125Q19.825 21 19 21M9 14Q8.575 14 8.2875 13.7125Q8 13.425 8 13Q8 12.575 8.2875 12.2875Q8.575 12 9 12Q9.425 12 9.7125 12.2875Q10 12.575 10 13Q10 13.425 9.7125 13.7125Q9.425 14 9 14M15 14Q14.575 14 14.2875 13.7125Q14 13.425 14 13Q14 12.575 14.2875 12.2875Q14.575 12 15 12Q15.425 12 15.7125 12.2875Q16 12.575 16 13Q16 13.425 15.7125 13.7125Q15.425 14 15 14M6.025 12.45Q5.85 9.8 7.625 7.9Q9.4 6 12.05 6Q14.275 6 15.9625 7.4125Q17.65 8.825 18 11.025Q15.725 11 13.8125 9.8Q11.9 8.6 10.875 6.55Q10.475 8.55 9.1875 10.1125Q7.9 11.675 6.025 12.45" />
    </svg>
  );
}

/**
 * Material Symbols Rounded "psychology" — a brain with a center cog/gear.
 */
function MaterialPsychology({ size = 18, style }: MaterialGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      fillRule="evenodd"
      style={style}
      aria-hidden="true"
    >
      <path d="M10.85 13.75L10.95 14.55Q10.975 14.75 11.1125 14.875Q11.25 15 11.45 15H12.55Q12.75 15 12.8875 14.875Q13.025 14.75 13.05 14.55L13.15 13.75Q13.35 13.675 13.5125 13.575Q13.675 13.475 13.8 13.35L14.55 13.675Q14.725 13.75 14.9 13.7Q15.075 13.65 15.175 13.475L15.725 12.525Q15.825 12.35 15.7875 12.175Q15.75 12 15.6 11.875L14.95 11.4Q15 11.2 15 11Q15 10.8 14.95 10.6L15.6 10.125Q15.75 10 15.7875 9.825Q15.825 9.65 15.725 9.475L15.175 8.525Q15.075 8.35 14.9 8.3Q14.725 8.25 14.55 8.325L13.8 8.65Q13.675 8.525 13.5125 8.425Q13.35 8.325 13.15 8.25L13.05 7.45Q13.025 7.25 12.8875 7.125Q12.75 7 12.55 7H11.45Q11.25 7 11.1125 7.125Q10.975 7.25 10.95 7.45L10.85 8.25Q10.65 8.325 10.4875 8.425Q10.325 8.525 10.2 8.65L9.45 8.325Q9.275 8.25 9.1 8.3Q8.925 8.35 8.825 8.525L8.275 9.475Q8.175 9.65 8.2125 9.825Q8.25 10 8.4 10.125L9.05 10.6Q9 10.8 9 11Q9 11.2 9.05 11.4L8.4 11.875Q8.25 12 8.2125 12.175Q8.175 12.35 8.275 12.525L8.825 13.475Q8.925 13.65 9.1 13.7Q9.275 13.75 9.45 13.675L10.2 13.35Q10.325 13.475 10.4875 13.575Q10.65 13.675 10.85 13.75M12 12.5Q11.375 12.5 10.9375 12.0625Q10.5 11.625 10.5 11Q10.5 10.375 10.9375 9.9375Q11.375 9.5 12 9.5Q12.625 9.5 13.0625 9.9375Q13.5 10.375 13.5 11Q13.5 11.625 13.0625 12.0625Q12.625 12.5 12 12.5M6 17.7Q4.575 16.4 3.7875 14.6625Q3 12.925 3 11Q3 7.25 5.625 4.625Q8.25 2 12 2Q15.125 2 17.5375 3.8375Q19.95 5.675 20.675 8.625L21.975 13.75Q22.1 14.225 21.8 14.6125Q21.5 15 21 15H19V18Q19 18.825 18.4125 19.4125Q17.825 20 17 20H15V21Q15 21.425 14.7125 21.7125Q14.425 22 14 22Q13.575 22 13.2875 21.7125Q13 21.425 13 21V19Q13 18.575 13.2875 18.2875Q13.575 18 14 18H17Q17 18 17 18Q17 18 17 18V14Q17 13.575 17.2875 13.2875Q17.575 13 18 13H19.7L18.75 9.125Q18.175 6.85 16.3 5.425Q14.425 4 12 4Q9.1 4 7.05 6.025Q5 8.05 5 10.95Q5 12.45 5.6125 13.8Q6.225 15.15 7.35 16.2L8 16.8V21Q8 21.425 7.7125 21.7125Q7.425 22 7 22Q6.575 22 6.2875 21.7125Q6 21.425 6 21M12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13Q12.35 13 12.35 13" />
    </svg>
  );
}

/**
 * Icon component — renders a lucide-react SVG.
 *
 * The API (name + className + size) matches the previous Material Symbols version
 * so the ~95 call sites keep working. The `fill` prop maps to `fill="currentColor"`
 * for filled-style icons (most lucide icons stay outlined regardless).
 */
export function Icon({ name, size = 18, fill = 0, className, title, onClick, style }: MaterialIconProps) {
  const Component = KNOWN_ICONS[name] ?? ICON_FALLBACK;
  if (!KNOWN_ICONS[name] && import.meta.env.DEV) {
    // Surface unknown names during development so we can add missing entries.
    console.warn(`[Icons] Unknown icon name: "${name}" — falling back to HelpCircle`);
  }
  const fillStyle: CSSProperties =
    fill === 1
      ? { fill: 'currentColor', stroke: 'currentColor', strokeWidth: 0 }
      : {};
  return (
    <span
      className={`icon-wrap ${className ?? ''}`.trim()}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        lineHeight: 1,
        userSelect: 'none',
        ...style,
      }}
      aria-hidden="true"
      title={title}
      onClick={onClick}
    >
      <Component size={size} style={fillStyle} />
    </span>
  );
}

// ── Named exports (semantic aliases, same as before) ──────────────────

export function EditIcon({ size = 18, className }: IconProps) {
  return <Icon name="edit" size={size} className={className} />;
}

export function TrashMultiIcon({ size = 18, className }: IconProps) {
  return <Icon name="delete" size={size} className={className} />;
}

export function RenameIcon({ size = 18, className }: IconProps) {
  return <Icon name="drive_file_rename" size={size} className={className} />;
}

export function NewFileIcon({ size = 18, className }: IconProps) {
  return <Icon name="note_add" size={size} className={className} />;
}

export function NewFolderIcon({ size = 18, className }: IconProps) {
  return <Icon name="create_new_folder" size={size} className={className} />;
}

// ── Files & Folders ───────────────────────────────────────────────────

export function FolderOpenIcon({ size = 18, className }: IconProps) {
  return <Icon name="folder_open" size={size} className={className} />;
}

export function FolderClosedIcon({ size = 18, className }: IconProps) {
  return <Icon name="folder" size={size} className={className} />;
}

// ── Collapse / Status ─────────────────────────────────────────────────

export function ChevronRightIcon({ size = 16, className }: IconProps) {
  return <Icon name="chevron_right" size={size} className={className} />;
}

export function ChevronDownIcon({ size = 16, className }: IconProps) {
  return <Icon name="expand_more" size={size} className={className} />;
}

export function ReasoningIcon({ size = 16, className }: IconProps) {
  return <Icon name="lightbulb" size={size} className={className} />;
}