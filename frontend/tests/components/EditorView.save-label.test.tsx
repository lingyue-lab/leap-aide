import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import EditorView from '@/components/shared/EditorView';

function renderEditor(extra: { saving?: boolean } = {}) {
  return render(
    <EditorView
      content="hello"
      onChange={() => {}}
      fileName="note.txt"
      onSave={() => {}}
      onCancel={() => {}}
      {...extra}
    />
  );
}

describe('EditorView — save button labels', () => {
  it('labels the button Save at rest', () => {
    renderEditor();
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('labels the button Saving... while the request is in flight', () => {
    renderEditor({ saving: true });
    expect(screen.getByRole('button', { name: 'Saving...' })).toBeInTheDocument();
  });

  it('does not add a second success label: confirmation is the snackbar’s job', () => {
    renderEditor();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument();
  });
});
