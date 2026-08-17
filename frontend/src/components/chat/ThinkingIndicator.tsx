/**
 * Three-dot "thinking" indicator — shown during streaming.
 * Three small dots bounce sequentially to signal that the agent is composing.
 */
export default function ThinkingIndicator() {
  return (
    <div className="thinking-indicator" role="status" aria-label="Thinking">
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
    </div>
  );
}
