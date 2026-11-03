import { renderMarkdown, highlightCode, resolveLanguage, relativeFileLinkTarget } from '@/utils/markdown';
import CodeCopyButton from './CodeCopyButton';
import LoadingSpinner from '@/components/common/LoadingSpinner';

// ── Types ────────────────────────────────────────────────────────

export type FileType = 'markdown' | 'text' | 'binary' | 'image';

export interface FileViewProps {
  /** Raw file content */
  content: string | null;
  /** Detected file type */
  fileType: FileType;
  /** File path (used for image alt text) */
  filePath?: string;

  // States
  loading?: boolean;
  error?: string | null;

  // Editing (controlled by parent)
  editing?: boolean;
  editContent?: string;
  onEditChange?: (val: string) => void;
  // Editing toolbar callbacks
  onSave?: () => void | Promise<void>;
  onCancel?: () => void;
  saving?: boolean;
  editError?: string | null;

  // Empty state
  emptyMessage?: string;

  // Relative file links in rendered markdown (e.g. [中文](SKILL.zh-CN.md))
  // are invalid SPA URLs; when provided, clicks are routed here instead.
  onRelativeLink?: (path: string) => void;

  // Additional class
  className?: string;
}

// ── Content-based binary detection ────────────────────────────────

/**
 * Heuristic to detect binary content.
 * Uses the same approach as git: checks for null bytes in the first N bytes.
 * Also checks for Unicode replacement characters (U+FFFD) which appear
 * when binary data is decoded as UTF-8 via response.text().
 */
const BINARY_SAMPLE_SIZE = 1024;
const BINARY_RATIO_THRESHOLD = 0.1;

function isBinaryContent(content: string): boolean {
  const sample = content.length > BINARY_SAMPLE_SIZE
    ? content.substring(0, BINARY_SAMPLE_SIZE)
    : content;

  // Null byte → definitely binary (standard git / ripgrep heuristic)
  if (sample.includes('\0')) return true;

  // Count replacement chars (from failed UTF-8 decode) + non-whitespace control chars
  let suspicious = 0;
  for (let i = 0; i < sample.length; i++) {
    const code = sample.charCodeAt(i);
    // U+FFFD replacement character
    if (code === 0xfffd) { suspicious++; continue; }
    // Control chars excluding tab(0x09), lf(0x0a), cr(0x0d)
    if (code < 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d) { suspicious++; continue; }
    // DEL
    if (code === 0x7f) { suspicious++; }
  }

  return suspicious > sample.length * BINARY_RATIO_THRESHOLD;
}

// ── Known extensions (fast path when content is unavailable) ──────

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico']);
const MARKDOWN_EXTS = new Set(['md', 'markdown']);

/**
 * Detection priority:
 *  1. Image / markdown by extension (always reliable)
 *  2. PDF by extension or magic bytes → binary
 *  3. Content-based binary detection (null byte + replacement char)
 *  4. Extension-based fallback (when content is unavailable)
 */
export function detectFileType(filePath: string, content?: string | null): FileType {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';

  // Image & markdown are unambiguous by extension
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (MARKDOWN_EXTS.has(ext)) return 'markdown';

  // PDF: extension or magic bytes
  if (ext === 'pdf' || content?.startsWith('%PDF-')) return 'binary';

  // Content-based binary detection (most reliable)
  if (content != null) {
    return isBinaryContent(content) ? 'binary' : 'text';
  }

  // Extension-only fallback when content not yet loaded
  // Covers common text formats; anything else defaults to binary
  const TEXT_EXTS = new Set([
    'txt', 'py', 'js', 'ts', 'tsx', 'jsx', 'json', 'yaml', 'yml', 'toml',
    'sh', 'bash', 'css', 'html', 'xml', 'csv', 'ini', 'cfg', 'conf',
    'log', 'env', 'gitignore', 'dockerfile', 'makefile',
    'c', 'cpp', 'h', 'hpp', 'rs', 'go', 'java', 'rb', 'php', 'sql',
    'vue', 'svelte', 'scss', 'less',
  ]);
  if (TEXT_EXTS.has(ext)) return 'text';

  return 'binary';
}

/** Whether a FileType supports in-browser editing */
export function isEditable(fileType: FileType): boolean {
  return fileType === 'markdown' || fileType === 'text';
}

// ── Component ────────────────────────────────────────────────────

import EditorView from './EditorView';

export default function FileView({
  content,
  fileType,
  filePath,
  loading,
  error,
  editing,
  editContent,
  onEditChange,
  onSave,
  onCancel,
  saving,
  editError,
  emptyMessage,
  onRelativeLink,
  className = '',
}: FileViewProps) {
  // ── Loading ──
  if (loading) {
    return (
      <div className={`flex-1 overflow-y-auto p-4${className ? ` ${className}` : ''}`}>
        <div className="max-w-[768px] mx-auto">
          <LoadingSpinner mode="inline" minimal />
        </div>
      </div>
    );
  }

  // ── Error ──
  if (error) {
    return (
      <div className={`flex-1 overflow-y-auto p-4${className ? ` ${className}` : ''}`}>
        <div className="max-w-[768px] mx-auto">
          <div className="text-base text-[var(--accent)] text-center py-8"
            style={{ fontWeight: 400, letterSpacing: '0.25px' }}>
            {error}
          </div>
        </div>
      </div>
    );
  }

  // ── Empty / no content ──
  if (content === null && !editing) {
    return (
      <div className={`flex-1 overflow-y-auto p-4${className ? ` ${className}` : ''}`}>
        <div className="max-w-[768px] mx-auto">
          <div className="text-base text-[var(--muted)] text-center py-8"
            style={{ fontWeight: 400, letterSpacing: '0.25px' }}>
            {emptyMessage || 'No content'}
          </div>
        </div>
      </div>
    );
  }

  // ── Editing mode ──
  if (editing) {
    return (
      <EditorView
        content={editContent || ''}
        onChange={(v) => onEditChange?.(v)}
        fileName={filePath?.split('/').pop() || 'file'}
        onSave={onSave}
        onCancel={onCancel}
        saving={saving}
        error={editError || undefined}
      />
    );
  }

  // ── Content wrapper ──
  const wrapper = (children: React.ReactNode) => (
    <div className={`flex-1 overflow-y-auto p-4${className ? ` ${className}` : ''}`}>
      <div className="max-w-[768px] mx-auto">
        {children}
      </div>
    </div>
  );

  // ── Image ──
  if (fileType === 'image') {
    return wrapper(
      <img
        src={content || ''}
        alt={filePath || 'image'}
        className="max-w-full max-h-[70vh] mx-auto"
      />
    );
  }

  // ── Markdown ──
  if (fileType === 'markdown') {
    const html = content ? renderMarkdown(content) : '';
    const handleLinkClick = (e: React.MouseEvent<HTMLDivElement>) => {
      const target = relativeFileLinkTarget(e);
      if (!target || !onRelativeLink) return;
      e.preventDefault();
      onRelativeLink(target);
    };
    return wrapper(
      <div className="file-view-code">
        <CodeCopyButton content={content || ''} />
        <div
          className="prose prose-sm text-[var(--foreground)]"
          onClick={onRelativeLink ? handleLinkClick : undefined}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    );
  }

  // ── Text ──
  if (fileType === 'text') {
    const lang = resolveLanguage(filePath);
    const html = content ? highlightCode(content, lang) : '';
    return wrapper(
      <div className="file-view-code">
        <CodeCopyButton content={content || ''} />
        <pre className="text-sm font-mono text-[var(--pre-text)] whitespace-pre-wrap break-words bg-[var(--code-bg)] p-3 rounded">
          <code dangerouslySetInnerHTML={{ __html: html }} />
        </pre>
      </div>
    );
  }

  // ── Binary ──
  return wrapper(
    <div className="text-base text-[var(--muted)] text-center py-8"
      style={{ fontWeight: 400, letterSpacing: '0.25px' }}>
      二进制格式，不支持预览和编辑
    </div>
  );
}
