
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FileTree from '@/components/workspace/FileTree';
import FilePreview from '@/components/workspace/FilePreview';
import BreadcrumbBar from '@/components/workspace/BreadcrumbBar';

interface RightPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function RightPanel({ isOpen, onClose }: RightPanelProps) {
  const { t } = useTranslation('workspace');
  const { currentDir, gitInfo, previewPath } = useWorkspaceStore();

  const handleNavigateUp = () => {
    const parts = currentDir.split('/').filter(Boolean);
    if (parts.length > 0) {
      parts.pop();
      useWorkspaceStore.getState().loadDir('/' + parts.join('/') || '.');
    }
  };

  return (
    <aside className={`right-panel ${isOpen ? 'open' : ''}`}>
      <div className="resize-handle" id="rightpanelResize" />
      
      <div className="panel-header">
        <span>{t('workspace.title')}</span>
        {gitInfo?.branch && (
          <span className="git-badge">{gitInfo.branch}</span>
        )}
        <div className="panel-actions">
          <button
            className="panel-icon-btn"
            onClick={onClose}
            title={t('workspace.hide_panel')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <button
            className="panel-icon-btn"
            onClick={handleNavigateUp}
            title={t('workspace.parent_dir')}
            style={{ display: currentDir === '.' ? 'none' : 'flex' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => {/* New file */}}
            title={t('workspace.new_file')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => {/* New folder */}}
            title={t('workspace.new_folder')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => useWorkspaceStore.getState().loadDir('.')}
            title={t('workspace.refresh')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          </button>
          <button
            className="panel-icon-btn close-preview"
            onClick={() => useWorkspaceStore.getState().setPreview(null)}
            title={t('workspace.close_preview')}
            style={{ display: previewPath ? 'flex' : 'none' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
          <button
            className="panel-icon-btn mobile-close-btn"
            onClick={onClose}
            title={t('workspace.close')}
          >
            ×
          </button>
        </div>
      </div>
      
      <BreadcrumbBar />
      
      <FileTree />
      
      <FilePreview />
    </aside>
  );
}
