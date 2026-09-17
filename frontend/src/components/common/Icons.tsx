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
  // Settings & system
  Puzzle,
  Sun,
  Lock,
  Monitor,
  Save,
  Bot,
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
// ── Material Symbols Rounded (subset font) ─────────────────────────────
//
// The sidebar nav icons render through this path so the visual style is
// consistent: filled Material Symbols Rounded glyphs sized via `font-size`,
// with the FILL axis driven by the `fill` prop (0 = outlined, 1 = filled).
//
// The font is subsetted to just these 6 ligatures (~180 KB vs ~5 MB
// upstream) — see scripts/subset-material-icons.sh. Keep this set and the
// LIGATURES list in that script in sync.

/**
 * Renders a single Material Symbols Rounded ligature. The `name` prop is the
 * ligature string itself (e.g. "calendar_month"); the browser substitutes
 * it for the matching glyph via the font's `rlig` lookups.
 */
function MaterialFontIcon({ name, size = 24, fill = 0, className, title, onClick, style }: MaterialIconProps) {
  const opsz = typeof size === 'number' ? size : 24;
  return (
    <span
      className={`material-symbols-rounded ${className ?? ''}`.trim()}
      style={{
        fontSize: size,
        fontVariationSettings: `'FILL' ${fill}, 'wght' 400, 'GRAD' 0, 'opsz' ${opsz}`,
        lineHeight: 1,
        userSelect: 'none',
        ...style,
      }}
      aria-hidden="true"
      title={title}
      onClick={onClick}
    >
      {name}
    </span>
  );
}

/**
 * Bind a Material ligature name to a MaterialFontIcon instance so it can be
 * dropped into the KNOWN_ICONS map alongside lucide components.
 */
function makeMaterialIcon(ligature: string): React.ComponentType<MaterialIconProps> {
  const Component = (props: MaterialIconProps) => <MaterialFontIcon {...props} name={ligature} />;
  Component.displayName = `Material(${ligature})`;
  return Component;
}

const MATERIAL_FONT_ICONS = new Set([
  'support_agent',
  'psychology',
  'calendar_month',
  'layers',
  'folder_open',
  'terminal_2',
  'settings',
  'logout',
]);

// Component shapes vary across icon backends:
//   • lucide SVG icons accept a subset of MaterialIconProps (size, style, ...).
//   • MaterialFontIcon accepts the full MaterialIconProps (size, fill, name, ...).
//   `any` keeps the registry flexible without contorting the union.
const KNOWN_ICONS: Record<string, React.ComponentType<any>> = {
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
  folder_open: makeMaterialIcon('folder_open'),
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
  calendar_month: makeMaterialIcon('calendar_month'),

  // Settings & system
  extension: Puzzle,
  light_mode: Sun,
  lock: Lock,
  logout: makeMaterialIcon('logout'),
  monitor: Monitor,
  save: Save,
  settings: makeMaterialIcon('settings'),
  smart_toy: Bot,
  support_agent: makeMaterialIcon('support_agent'),
  terminal_2: makeMaterialIcon('terminal'),

  // Chat & people
  chat_bubble: MessageCircle,
  mic: Mic,
  person: User,
  psychology: makeMaterialIcon('psychology'),
  send: Send,

  // Common actions
  compress: Minimize2,
  refresh: RefreshCw,
  search: Search,

  // Marker / state
  keep: Pin,
  layers: makeMaterialIcon('layers'),
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

// ── Icon dispatcher ───────────────────────────────────────────────────

/**
 * Icon component — renders a lucide-react SVG (with 6 Material Symbols
 * Rounded font icons rendered via `MaterialFontIcon`; see
 * `MATERIAL_FONT_ICONS`).
 *
 * The API (name + className + size) matches the previous Material Symbols version
 * so the ~95 call sites keep working.
 *
 * Two rendering paths:
 *   • lucide (SVG): `fill=1` widens the stroke so the glyph reads as "active".
 *     lucide has no filled variants — applying `fill: currentColor` +
 *     `strokeWidth: 0` would collapse complex icons (Calendar, Mic) into
 *     solid blobs because the inner strokes vanish and closed paths fill in.
 *   • Material font (text ligature): the `fill` prop drives the FILL axis
 *     via `fontVariationSettings` inside `MaterialFontIcon`. The lucide
 *     `fillStyle` override is skipped for these (it has no effect on text).
 *
 * The visual highlight comes mostly from the caller's CSS (color change +
 * background pill, see `.nav-tab.active`).
 */
export function Icon({ name, size = 18, fill = 0, className, title, onClick, style }: MaterialIconProps) {
  const Component = KNOWN_ICONS[name] ?? ICON_FALLBACK;
  if (!KNOWN_ICONS[name] && import.meta.env.DEV) {
    // Surface unknown names during development so we can add missing entries.
    console.warn(`[Icons] Unknown icon name: "${name}" — falling back to HelpCircle`);
  }
  const isMaterialFont = MATERIAL_FONT_ICONS.has(name);
  // lucide path: bolder stroke on active. Material path: no override — its
  // own component reads `fill` and writes the FILL axis to fontVariationSettings.
  const fillStyle: CSSProperties =
    fill === 1 && !isMaterialFont ? { strokeWidth: 2.5, fill: 'none' } : {};
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