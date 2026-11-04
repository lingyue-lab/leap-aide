import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import Editor from 'react-simple-code-editor';
import { resolveLanguage, highlightCode } from '@/utils/markdown';

export interface EditorViewProps {
  content: string;
  onChange: (value: string) => void;
  fileName?: string;
  language?: string;
  onSave?: () => void | Promise<void>;
  onCancel?: () => void;
  saving?: boolean;
  error?: string;
}

export default function EditorView({
  content,
  onChange,
  fileName,
  language,
  onSave,
  onCancel,
  saving,
  error,
}: EditorViewProps) {
  const { t } = useTranslation('common');
  const lang = resolveLanguage(fileName, language);
  const highlight = useCallback(
    (code: string) => highlightCode(code, lang),
    [lang]
  );

  const hasToolbar = !!(onSave || onCancel);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      {hasToolbar && (
        <div className="topbar">
          <div className="flex-1 min-w-0 overflow-hidden">
            <div className="topbar-title">
              {fileName || 'Editing'}
            </div>
            <div className="topbar-meta">editing</div>
          </div>
          <div className="topbar-chips">
            {error && (
              <span className="text-sm text-[var(--accent)] self-center">{error}</span>
            )}
            {onSave && (
              <button
                className="cron-btn run text-sm"
                onClick={onSave}
                disabled={saving}
              >
                {saving ? t('common.saving') : t('common.save')}
              </button>
            )}
            {onCancel && (
              <button
                className="cron-btn text-sm"
                onClick={onCancel}
              >
                {t('common.cancel')}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Editor */}
      <div className="flex-1 overflow-y-auto p-4">
        <div className="max-w-[768px] mx-auto">
          <Editor
            value={content}
            onValueChange={onChange}
            highlight={highlight}
            padding={16}
            tabSize={2}
            style={{
              fontFamily: '"Fira Code", "Fira Mono", monospace',
              fontSize: 16,
              lineHeight: 1.6,
              minHeight: '100%',
              background: 'var(--bg)',
              color: 'var(--foreground)',
              border: '1px solid var(--border2)',
              borderRadius: 8,
              outline: 'none',
            }}
            textareaClassName="no-focus-ring"
          />
        </div>
      </div>
    </div>
  );
}
