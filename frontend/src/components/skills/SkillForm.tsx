import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { skillsService } from '@/services/api';
import { useSkillStore } from '@/stores/useSkillStore';
import FormPage, { FormActions } from '@/components/common/FormPage';
import { FormField, TextField } from '@/components/common/FormField';
import { Icon } from '@/components/common/Icons';

interface FileEntry {
  path: string; // relative path within the selected directory, e.g. "prompts/base.md"
  file: File;
}

interface SkillFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

// ── Build a simple display tree from a flat file list ──────────────────────
interface TreeNode {
  name: string;
  isDir: boolean;
  children: TreeNode[];
}

function buildTree(files: FileEntry[]): TreeNode {
  const root: TreeNode = { name: '', isDir: true, children: [] };
  for (const { path } of files) {
    const parts = path.split('/');
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLast = i === parts.length - 1;
      let child = node.children.find((c) => c.name === part);
      if (!child) {
        child = { name: part, isDir: !isLast, children: [] };
        node.children.push(child);
      }
      if (!isLast) node = child;
    }
  }
  return root;
}

const MAX_PREVIEW_NODES = 60;

function TreeNodeView({ node, depth, counter }: { node: TreeNode; depth: number; counter: { n: number } }) {
  const [open, setOpen] = useState(depth < 2);
  if (counter.n >= MAX_PREVIEW_NODES) return null;
  counter.n++;

  const indent = depth * 14;
  if (node.isDir && node.name) {
    return (
      <div>
        <div
          className="flex items-center gap-1 cursor-pointer select-none py-0.5 hover:text-[var(--blue)]"
          style={{ paddingLeft: indent }}
          onClick={() => setOpen(!open)}
        >
          <Icon name="keyboard_arrow_down" size={11} className={`flex-shrink-0 transition-transform ${open ? '' : '-rotate-90'}`} style={{ opacity: 0.6 }} />
          <Icon name="folder_open" size={18} className="flex-shrink-0 text-[var(--blue)]" style={{ opacity: 0.8 }} />
          <span className="text-sm">{node.name}</span>
        </div>
        {open && node.children.map((c) => (
          <TreeNodeView key={c.name} node={c} depth={depth + 1} counter={counter} />
        ))}
      </div>
    );
  }
  // file
  const isSkillMd = node.name === 'SKILL.md';
  return (
    <div
      className="flex items-center gap-1 py-0.5"
      style={{ paddingLeft: indent + (node.isDir ? 0 : 4) }}
    >
      <Icon name="description" size={14} className="flex-shrink-0" style={{ opacity: 0.5 }} />
      <span className={`text-sm ${isSkillMd ? 'text-[var(--blue)] font-medium' : ''}`}>{node.name}</span>
      {isSkillMd && <span className="text-[10px] text-[var(--blue)] opacity-70">✓</span>}
    </div>
  );
}

export default function SkillForm({ onSuccess, onCancel }: SkillFormProps) {
  const { t } = useTranslation('common');
  const loadSkills = useSkillStore((s) => s.loadSkills);
  const selectSkill = useSkillStore((s) => s.selectSkill);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [skillName, setSkillName] = useState('');
  const [category, setCategory] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const hasSkillMd = files.some(({ path }) => path === 'SKILL.md' || path.endsWith('/SKILL.md'));
  const rootDirName = files.length > 0 ? files[0].path.split('/')[0] : '';

  // ── File input handling ──────────────────────────────────────────────────

  const processFileList = (fileList: FileList) => {
    const entries: FileEntry[] = [];
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      // webkitRelativePath: "my-skill/SKILL.md" or "my-skill/prompts/base.md"
      const rel = f.webkitRelativePath || f.name;
      // Strip the root directory prefix so path is relative to skill root
      const parts = rel.split('/');
      const pathWithinSkill = parts.length > 1 ? parts.slice(1).join('/') : rel;
      if (pathWithinSkill) {
        entries.push({ path: pathWithinSkill, file: f });
      }
    }
    setFiles(entries);
    setConflict(false);
    setError('');
    // Auto-fill skill name from directory name
    if (entries.length > 0) {
      const dirName = fileList[0].webkitRelativePath?.split('/')[0] || '';
      if (dirName) setSkillName(dirName.toLowerCase().replace(/\s+/g, '-'));
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      processFileList(e.target.files);
    }
  };

  // Drag & drop (directories aren't easily supported via drop in all browsers,
  // but we handle the case where the browser does support it via dataTransfer.items)
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };
  const handleDragLeave = () => setIsDragging(false);
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFileList(e.dataTransfer.files);
    }
  };

  // ── Import ───────────────────────────────────────────────────────────────

  const doImport = async (overwrite = false) => {
    if (!skillName.trim()) {
      setError('Skill name is required');
      return;
    }
    if (files.length === 0) {
      setError('No files selected');
      return;
    }
    setUploading(true);
    setError('');
    setConflict(false);

    try {
      const result = await skillsService.importDir(
        effectiveSkillName,
        category.trim() || undefined,
        files,
        overwrite
      );
      if (result.conflict) {
        setConflict(true);
        setUploading(false);
        return;
      }
      // Success: reload skill list and navigate to the new skill
      await loadSkills();
      const newSkillName = result.name || effectiveSkillName;
      // Try to find and select the newly imported skill
      const updatedSkills = useSkillStore.getState().skills;
      const imported = updatedSkills.find(
        (s) => s.dir_name === newSkillName || s.name === newSkillName
      );
      if (imported) {
        selectSkill(imported);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : (err as { message?: string })?.message || 'Import failed');
    } finally {
      setUploading(false);
    }
  };

  // skill name defaults to the uploaded dir name; no explicit input required
  const effectiveSkillName = skillName.trim() || rootDirName.toLowerCase().replace(/\s+/g, '-');
  const canImport = files.length > 0 && effectiveSkillName.length > 0 && !uploading;

  // ── Tree preview ─────────────────────────────────────────────────────────
  const tree = files.length > 0 ? buildTree(files) : null;
  const counter = { n: 0 };

  return (
    <FormPage
      title={t('skills.new') || 'New Skill'}
      error={conflict ? '' : error}
      actions={
        <FormActions
          onSubmit={() => doImport(false)}
          onCancel={onCancel}
          busy={uploading}
          submitDisabled={!canImport}
        />
      }
    >

        {/* Drop zone */}
        <div
          className={`border-2 border-dashed rounded-lg flex flex-col items-center justify-center py-8 px-4 cursor-pointer transition-colors select-none ${
            isDragging
              ? 'border-[var(--blue)] bg-[color-mix(in_srgb,var(--blue)_8%,transparent)]'
              : files.length > 0
              ? 'border-[var(--border)] bg-[var(--hover-bg)]'
              : 'border-[var(--border2)] hover:border-[var(--blue)] hover:bg-[color-mix(in_srgb,var(--blue)_5%,transparent)]'
          }`}
          onClick={() => fileInputRef.current?.click()}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {files.length > 0 ? (
            <>
              <Icon name="folder_open" size={32} className="text-[var(--blue)] mb-2" />
              <div className="text-sm font-medium text-[var(--foreground)]">
                {rootDirName || skillName}
              </div>
              <div className="text-sm text-[var(--muted)] mt-0.5">
                {files.length} file{files.length !== 1 ? 's' : ''} selected · click to change
              </div>
            </>
          ) : (
            <>
              <Icon name="folder_open" size={37} className="text-[var(--muted)] mb-2" />
              <div className="text-sm font-medium text-[var(--foreground)]">
                Click to select a directory
              </div>
              <div className="text-sm text-[var(--muted)] mt-1">or drag & drop a skill folder here</div>
            </>
          )}
        </div>

        {/* Hidden file input — webkitdirectory for directory picking */}
        <input
          ref={fileInputRef}
          type="file"
          // @ts-ignore — webkitdirectory is non-standard but widely supported
          webkitdirectory=""
          multiple
          style={{ display: 'none' }}
          onChange={handleFileInputChange}
        />

        {/* Skill name + category — stacked vertically */}
        <div className="flex flex-col gap-3">
          <FormField
            label="Skill Name"
            hint={`(optional — defaults to folder name${rootDirName ? `: "${rootDirName}"` : ''})`}
          >
            <TextField
              type="text"
              placeholder={rootDirName || 'e.g. my-skill'}
              value={skillName}
              onChange={(e) => { setSkillName(e.target.value); setConflict(false); setError(''); }}
            />
          </FormField>
          <FormField label="Category" hint="(optional)">
            <TextField
              type="text"
              placeholder="e.g. devops"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </FormField>
        </div>

        {/* Warnings */}
        {files.length > 0 && !hasSkillMd && (
          <div className="flex items-center gap-2 text-sm text-[color:var(--yellow,#d97706)] bg-[color-mix(in_srgb,var(--yellow,#d97706)_10%,transparent)] border border-[color-mix(in_srgb,var(--yellow,#d97706)_30%,transparent)] rounded px-3 py-2">
            <Icon name="warning" size={18} className="flex-shrink-0" />
            No <strong className="mx-0.5">SKILL.md</strong> found in the selected directory. The skill may not be usable until you add one.
          </div>
        )}

        {/* Conflict banner */}
        {conflict && (
          <div className="flex items-center gap-3 text-sm bg-[color-mix(in_srgb,var(--accent)_10%,transparent)] border border-[color-mix(in_srgb,var(--accent)_30%,transparent)] rounded px-3 py-2.5">
            <Icon name="warning" size={18} className="flex-shrink-0 text-[var(--accent)]" />
            <span className="flex-1 text-[var(--foreground)]">
              Skill <strong>"{skillName}"</strong> already exists.
            </span>
            <button
              className="cron-btn run text-sm flex-shrink-0"
              onClick={() => doImport(true)}
            >
              Overwrite
            </button>
            <button
              className="cron-btn text-sm flex-shrink-0"
              onClick={() => setConflict(false)}
            >
              Cancel
            </button>
          </div>
        )}

        {/* File tree preview */}
        {tree && tree.children.length > 0 && (
          <div>
            <div className="text-sm font-medium text-[var(--muted)] mb-1.5">
              Preview · {files.length} file{files.length !== 1 ? 's' : ''}
              {files.length > MAX_PREVIEW_NODES && (
                <span className="ml-1 opacity-60">(showing first {MAX_PREVIEW_NODES})</span>
              )}
            </div>
            <div
              className="border border-[var(--border2)] rounded p-2 text-[var(--foreground)] overflow-y-auto"
              style={{ maxHeight: 260, fontSize: 14, lineHeight: '1.7' }}
            >
              {tree.children.map((c) => (
                <TreeNodeView key={c.name} node={c} depth={0} counter={counter} />
              ))}
              {files.length > MAX_PREVIEW_NODES && (
                <div className="text-sm text-[var(--muted)] mt-1 pl-2 italic">
                  + {files.length - MAX_PREVIEW_NODES} more files…
                </div>
              )}
            </div>
          </div>
        )}
    </FormPage>
  );
}
