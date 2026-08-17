
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FileTree from '@/components/workspace/FileTree';
import FilePreview from '@/components/workspace/FilePreview';
import BreadcrumbBar from '@/components/workspace/BreadcrumbBar';
import { Icon } from '@/components/common/Icons';

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
            <Icon name="chevron_right" size={18} />
          </button>
          <button
            className="panel-icon-btn"
            onClick={handleNavigateUp}
            title={t('workspace.parent_dir')}
            style={{ display: currentDir === '.' ? 'none' : 'flex' }}
          >
            <Icon name="arrow_upward" size={18} />
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => {/* New file */}}
            title={t('workspace.new_file')}
          >
            <Icon name="add" size={18} />
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => {/* New folder */}}
            title={t('workspace.new_folder')}
          >
            <Icon name="folder_open" size={18} />
          </button>
          <button
            className="panel-icon-btn"
            onClick={() => useWorkspaceStore.getState().loadDir('.')}
            title={t('workspace.refresh')}
          >
            <Icon name="refresh" size={18} />
          </button>
          <button
            className="panel-icon-btn close-preview"
            onClick={() => useWorkspaceStore.getState().setPreview(null)}
            title={t('workspace.close_preview')}
            style={{ display: previewPath ? 'flex' : 'none' }}
          >
            <Icon name="close" size={18} />
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
