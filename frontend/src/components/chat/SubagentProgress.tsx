import { useState, useEffect } from 'react';
import { useSessionStore } from '@/stores/useSessionStore';

interface SubagentEvent {
  event_type: string;
  subagent_id?: string;
  child_session_id?: string;
  task_index?: number;
  goal?: string;
  preview?: string;
  status?: string;
  tool_name?: string;
  args?: Record<string, string>;
  model?: string;
  depth?: number;
  tool_count?: number;
  sessionId?: string;
  streamId?: string;
}

interface SubagentProgressProps {
  sessionId: string;
}

// Tool emoji mapping (matching CLI's get_tool_emoji)
const TOOL_EMOJI: Record<string, string> = {
  bash: '📝',
  run_shell_command: '📝',
  read_file: '📄',
  write_file: '✏️',
  edit_file: '✏️',
  glob: '🔍',
  grep: '🔍',
  list_files: '📁',
  web_search: '🌐',
  web_fetch: '🌐',
  search_files: '🔍',
  search_content: '🔍',
  session_search: '🔍',
  browser_navigate: '🌐',
  browser_click: '🖱️',
  browser_type: '⌨️',
  browser_screenshot: '📸',
  delegate_task: '🔀',
  write_todo: '📋',
  todo: '📋',
};

function getToolEmoji(toolName?: string): string {
  if (!toolName) return '🔧';
  return TOOL_EMOJI[toolName] || '🔧';
}

function truncate(str: string, maxLen: number): string {
  if (!str || str.length <= maxLen) return str || '';
  return str.slice(0, maxLen) + '…';
}

export default function SubagentProgress({ sessionId }: SubagentProgressProps) {
  const [events, setEvents] = useState<SubagentEvent[]>([]);
  const [isVisible, setIsVisible] = useState(false);
  const { loadSession } = useSessionStore();

  useEffect(() => {
    const handleSubagentEvent = (e: CustomEvent<SubagentEvent>) => {
      // Only show events for this child session
      if (e.detail.child_session_id !== sessionId) return;

      setEvents(prev => {
        const newEvents = [...prev, e.detail];
        // Keep only the last 100 events to avoid memory issues
        if (newEvents.length > 100) {
          return newEvents.slice(-100);
        }
        return newEvents;
      });

      // Show the component when we receive events
      if (!isVisible) {
        setIsVisible(true);
      }

      // When complete, trigger loadSession to get full content from state.db
      if (e.detail.event_type === 'subagent.complete') {
        setTimeout(() => {
          loadSession(sessionId);
        }, 500);
      }
    };

    window.addEventListener('subagent-progress', handleSubagentEvent as EventListener);

    return () => {
      window.removeEventListener('subagent-progress', handleSubagentEvent as EventListener);
    };
  }, [sessionId, isVisible, loadSession]);

  // Clear events when session changes
  useEffect(() => {
    setEvents([]);
    setIsVisible(false);
  }, [sessionId]);

  if (!isVisible || events.length === 0) {
    return null;
  }

  // Group events by subagent_id
  const groupedEvents = events.reduce<Record<string, SubagentEvent[]>>((acc, event) => {
    const key = event.subagent_id || 'unknown';
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(event);
    return acc;
  }, {});

  return (
    <div className="subagent-progress-container">
      {Object.entries(groupedEvents).map(([subagentId, subagentEvents]) => (
        <SubagentBlock
          key={subagentId}
          subagentId={subagentId}
          events={subagentEvents}
        />
      ))}
    </div>
  );
}

interface SubagentBlockProps {
  subagentId: string;
  events: SubagentEvent[];
}

function SubagentBlock({ events }: SubagentBlockProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  // Find the start event to get the goal
  const startEvent = events.find(e => e.event_type === 'subagent.start');
  const goal = startEvent?.goal || '子Agent任务';

  // Check if completed
  const completeEvent = events.find(e => e.event_type === 'subagent.complete');
  const isComplete = !!completeEvent;
  const hasError = completeEvent?.status === 'error';

  // Count tool calls
  const toolCalls = events.filter(e => e.event_type === 'subagent.tool');
  const toolCount = toolCalls.length;

  return (
    <div className={`subagent-block ${isComplete ? (hasError ? 'subagent-error' : 'subagent-complete') : 'subagent-running'}`}>
      <div
        className="subagent-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <span className="subagent-toggle">
          {isExpanded ? '▼' : '▶'}
        </span>
        <span className="subagent-icon">
          {isComplete ? (hasError ? '❌' : '✅') : '🔀'}
        </span>
        <span className="subagent-goal">
          {truncate(goal, 60)}
        </span>
        <span className="subagent-stats">
          {toolCount > 0 && (
            <span className="subagent-tool-count">
              {toolCount} 工具调用
            </span>
          )}
          {isComplete && (
            <span className="subagent-duration">
              完成
            </span>
          )}
        </span>
      </div>

      {isExpanded && (
        <div className="subagent-events">
          {events.map((event, index) => (
            <SubagentEventRow key={index} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}

interface SubagentEventRowProps {
  event: SubagentEvent;
}

function SubagentEventRow({ event }: SubagentEventRowProps) {
  switch (event.event_type) {
    case 'subagent.start':
      return (
        <div className="subagent-event subagent-start">
          <span className="subagent-event-icon">🔀</span>
          <span className="subagent-event-text">
            开始: {event.goal || ''}
          </span>
        </div>
      );

    case 'subagent.tool':
      return (
        <div className="subagent-event subagent-tool">
          <span className="subagent-event-prefix">├─</span>
          <span className="subagent-event-icon">
            {getToolEmoji(event.tool_name)}
          </span>
          <span className="subagent-event-tool-name">
            {event.tool_name}
          </span>
          {event.preview && (
            <span className="subagent-event-preview">
              "{truncate(event.preview, 50)}"
            </span>
          )}
        </div>
      );

    case 'subagent.text':
      return (
        <div className="subagent-event subagent-text">
          <span className="subagent-event-prefix">│</span>
          <span className="subagent-event-text">
            {event.preview || ''}
          </span>
        </div>
      );

    case 'subagent.thinking':
      return (
        <div className="subagent-event subagent-thinking">
          <span className="subagent-event-prefix">├─</span>
          <span className="subagent-event-icon">💭</span>
          <span className="subagent-event-text">
            "{truncate(event.preview || '', 50)}"
          </span>
        </div>
      );

    case 'subagent.progress':
      return (
        <div className="subagent-event subagent-progress-event">
          <span className="subagent-event-prefix">├─</span>
          <span className="subagent-event-icon">🔀</span>
          <span className="subagent-event-text">
            {event.preview || ''}
          </span>
        </div>
      );

    case 'subagent.complete':
      return (
        <div className="subagent-event subagent-complete-event">
          <span className="subagent-event-icon">
            {event.status === 'error' ? '❌' : '✅'}
          </span>
          <span className="subagent-event-text">
            {event.status === 'error' ? '失败' : '完成'}
            {event.preview ? `: ${truncate(event.preview, 50)}` : ''}
          </span>
        </div>
      );

    default:
      return null;
  }
}
