import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { clarifyService } from '@/services/api';
import type { ClarifyRequest } from '@/types';

export default function ClarifyCard() {
  const { t } = useTranslation('chat');
  const { busy, activeStreamId } = useChatStore();
  const [visible, setVisible] = useState(false);
  const [clarify, setClarify] = useState<ClarifyRequest | null>(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const handleClarify = (e: CustomEvent) => {
      setClarify(e.detail);
      setVisible(true);
    };

    window.addEventListener('clarify-required', handleClarify as EventListener);
    return () => {
      window.removeEventListener('clarify-required', handleClarify as EventListener);
    };
  }, []);

  useEffect(() => {
    if (!activeStreamId || !busy) {
      setVisible(false);
      setClarify(null);
      setInput('');
    }
  }, [activeStreamId, busy]);

  const handleSelectChoice = async (choice: string) => {
    if (!clarify) return;
    
    setLoading(true);
    try {
      await clarifyService.respond(clarify._session_id || '', choice);
      setVisible(false);
      setClarify(null);
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async () => {
    if (!clarify || !input.trim()) return;
    
    setLoading(true);
    try {
      await clarifyService.respond(clarify._session_id || '', input.trim());
      setVisible(false);
      setClarify(null);
      setInput('');
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  if (!visible || !clarify) return null;

  const choices = clarify.choices || clarify.choices_offered || [];

  return (
    <div className={`clarify-card ${visible ? 'visible' : ''}`}>
      <div className="clarify-inner">
        <div className="clarify-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          {t('clarify.title')}
        </div>
        
        {clarify.question && (
          <div className="clarify-question">{clarify.question}</div>
        )}
        
        {choices.length > 0 && (
          <div className="clarify-choices">
            {choices.map((choice, index) => (
              <button
                key={index}
                className={`clarify-choice ${index === choices.length - 1 ? 'other' : ''}`}
                onClick={() => handleSelectChoice(choice)}
                disabled={loading}
              >
                <span className="clarify-choice-badge">{index + 1}</span>
                <span className="clarify-choice-text">{choice}</span>
              </button>
            ))}
          </div>
        )}
        
        <div className="clarify-response">
          <input
            type="text"
            className="clarify-input"
            placeholder={t('clarify.other')}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
          <button
            className="clarify-submit"
            onClick={handleSubmit}
            disabled={loading || !input.trim()}
          >
            {loading ? '...' : t('clarify.submit')}
          </button>
        </div>
        
        <p className="clarify-hint">
          Press <kbd className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-mono bg-[rgba(255,255,255,0.08)] border border-[rgba(255,255,255,0.15)] rounded text-[var(--muted)]">1</kbd>-<kbd className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-mono bg-[rgba(255,255,255,0.08)] border border-[rgba(255,255,255,0.15)] rounded text-[var(--muted)]">{choices.length}</kbd> or type your answer
        </p>
      </div>
    </div>
  );
}
