import type { CSSProperties, MouseEventHandler } from 'react';

export interface IconProps {
  size?: number;
  className?: string;
}

export interface MaterialIconProps extends IconProps {
  /** Material Symbols ligature name, e.g. "close", "folder_open". */
  name: string;
  /** 0 = outlined, 1 = filled. Default outlined to match the previous stroke style. */
  fill?: 0 | 1;
  title?: string;
  onClick?: MouseEventHandler<HTMLSpanElement>;
  style?: CSSProperties;
}

/**
 * Generic Material Symbols icon (M3). Renders a `<span>` that uses the
 * Material Symbols Rounded variable font, so the glyph inherits `color`
 * from CSS (like the previous `stroke="currentColor"` SVGs did).
 */
export function Icon({ name, size = 18, fill = 0, className, title, onClick, style }: MaterialIconProps) {
  return (
    <span
      className={`material-symbols-rounded ${className ?? ''}`}
      style={{
        fontSize: size,
        fontVariationSettings: `'FILL' ${fill}, 'wght' 400, 'GRAD' 0, 'opsz' 24`,
        lineHeight: 1,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
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

// ── Actions ───────────────────────────────────────────────────────

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

// ── Files & Folders ───────────────────────────────────────────────

export function FolderOpenIcon({ size = 18, className }: IconProps) {
  return <Icon name="folder_open" size={size} className={className} />;
}

export function FolderClosedIcon({ size = 18, className }: IconProps) {
  return <Icon name="folder" size={size} className={className} />;
}

// ── Collapse / Status ─────────────────────────────────────────────

export function ChevronRightIcon({ size = 16, className }: IconProps) {
  return <Icon name="chevron_right" size={size} className={className} />;
}

export function ReasoningIcon({ size = 16, className }: IconProps) {
  return <Icon name="lightbulb" size={size} className={className} />;
}
