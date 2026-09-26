import { useEffect, useRef } from 'react';
import { useSystem } from '@/contexts/SystemContext';

export function TranscriptView() {
  const { transcript } = useSystem();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (endRef.current) {
      endRef.current.parentElement?.scrollTo({
        top: endRef.current.parentElement.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, [transcript.entries]);

  if (!transcript.enabled) {
    return (
      <div className="p-4 text-center text-muted-foreground text-sm">
        Transcript will appear here when conversation starts
      </div>
    );
  }

  if (transcript.entries.length === 0) {
    return (
      <div className="p-4 text-center text-muted-foreground text-sm">
        Waiting for conversation...
      </div>
    );
  }

  return (
    <div className="h-64 overflow-y-auto p-4 bg-muted/60 rounded-xl border border-border/60 space-y-2 font-mono text-sm">
      {transcript.entries.map((entry) => (
        <div key={entry.id} className="flex gap-2">
          <span
            className={`font-semibold min-w-[60px] ${
              entry.role === 'agent'
                ? 'text-purple-500'
                : entry.role === 'user'
                ? 'text-cyan-500'
                : 'text-muted-foreground'
            }`}
          >
            {entry.role === 'agent' ? 'Agent:' : entry.role === 'user' ? 'User:' : 'System:'}
          </span>

          <span className="flex-1">
            {entry.text}
            {!entry.final && <span className="text-yellow-500 ml-1">...</span>}
          </span>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
