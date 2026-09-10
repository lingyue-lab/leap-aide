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
          no_matching_commands: 'No matching commands',
          sessions: {
            subagent: 'Subagent',
            actions: 'Actions',
            confirm_delete: 'Delete this session?',
            compress_confirm: 'Compress this session?',
            clear_streaming_warn: 'Cannot clear a streaming session.',
          },
        },
      },
    },
  });

export default i18n;