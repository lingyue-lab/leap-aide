import { useState } from 'react';
import { copyText } from '@/utils/clipboard';
import { Icon } from '@/components/common/Icons';

export interface CodeCopyButtonProps {
  /** Text content to copy to the clipboard */
  content: string;
}

/**
 * "Copy" button floating over the top-right corner of file content
 * (revealed on hover via `.file-view-code:hover .file-view-copy-btn`).
 */
export default function CodeCopyButton({ content }: CodeCopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await copyText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      type="button"
      className={`file-view-copy-btn${copied ? ' copied' : ''}`}
      onClick={handleCopy}
      title="Copy"
      aria-label="Copy"
    >
      {copied ? (
        <Icon name="check" size={18} />
      ) : (
        <Icon name="content_copy" size={18} />
      )}
    </button>
  );
}
