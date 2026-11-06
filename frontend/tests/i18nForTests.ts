/**
 * Lightweight i18next bootstrap for vitest.
 *
 * jsdom + react-i18next: without `i18n.init()`, `useTranslation()` returns
 * the raw key (e.g. `workspace.title`). That makes screen.getByText fragile
 * to translation drift, so we initialise a small English resource bundle
 * that covers every namespace referenced by components in this directory.
 *
 * If a component under test introduces a new key, add it here — keep this
 * file minimal so test setup stays fast.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

void i18n
  .use(initReactI18next)
  .init({
    lng: 'en',
    fallbackLng: 'en',
    defaultNS: 'common',
    ns: ['common'],
    interpolation: { escapeValue: false },
    resources: {
      en: {
        workspace: {
          title: 'Workspace',
          hide_panel: 'Hide panel',
          parent_dir: 'Parent directory',
          new_file: 'New file',
          new_folder: 'New folder',
          refresh: 'Refresh',
          close_preview: 'Close preview',
          close: 'Close',
        },
        common: {
          // Components under test call t('common.save') etc.; the real
          // en/common.json nests these under a top-level `common` object.
          common: {
            save: 'Save',
            saving: 'Saving...',
            saved: 'Saved',
            cancel: 'Cancel',
          },
          no_matching_commands: 'No matching commands',
          ok: 'OK',
          add: 'Add',
          cancel: 'Cancel',
          adding: 'Adding…',
          workspaces: {
            new_workspace: 'New Workspace',
            form: {
              path_label: 'Workspace Path',
              path_hint: '(absolute path to a directory)',
              path_helper:
                'Start typing to see directory suggestions below. Click a suggestion (or use ↑↓ and Tab/Enter) to complete the path.',
              path_placeholder: '/home/user/my-project',
              name_label: 'Display Name',
              name_hint: '(optional — defaults to folder name)',
              name_placeholder: 'e.g. My Project',
              suggestions_title: 'Directory suggestions',
              error_path_required: 'Path is required',
              error_add_failed: 'Failed to add workspace',
            },
          },
          sessions: {
            subagent: 'Subagent',
            actions: 'Actions',
            confirm_delete: 'Delete this session?',
            compress_confirm: 'Compress this session?',
            clear_streaming_warn: 'Cannot clear a streaming session.',
          },
          providers: {
            gate: {
              title: 'Add an API key to get started',
              description: 'No model providers are configured yet.',
              configure: 'Configure providers',
            },
          },
        },
      },
    },
  });

export default i18n;
