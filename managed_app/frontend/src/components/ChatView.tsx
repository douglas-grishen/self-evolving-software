import { useState, useRef, useEffect, useCallback } from "react";
import "./ChatView.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

// ---------------------------------------------------------------------------
// Suggested questions
// ---------------------------------------------------------------------------

const SUGGESTIONS = [
  "¿Qué apps existen actualmente?",
  "¿Qué está tratando de construir el engine?",
  "¿Por qué están fallando las evoluciones?",
  "¿Cuál es el Purpose del sistema?",
  "¿Qué archivos ha creado o modificado el engine?",
  "Explain how the MAPE-K loop works",
];

// ---------------------------------------------------------------------------
// ChatView
// ---------------------------------------------------------------------------

export function ChatView() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "Hola! Soy tu asistente para este sistema. Tengo acceso en tiempo real a todo: el Purpose, las apps construidas, el historial de evoluciones, las inceptions, y cómo funciona la arquitectura.\n\n¿Qué quieres saber?",
    },
  ]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;

      const userMsg: Message = {
        id: Date.now().toString(),
        role: "user",
        content: trimmed,
      };
      const assistantId = (Date.now() + 1).toString();
      const pendingMsg: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
        pending: true,
      };

      setMessages((prev) => [...prev, userMsg, pendingMsg]);
      setInput("");
      setIsStreaming(true);

      // Build history (exclude pending placeholder)
      const history = [...messages, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));

      abortRef.current = new AbortController();

      try {
        const res = await fetch("/api/v1/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: history }),
          signal: abortRef.current.signal,
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (!res.body) throw new Error("No response body");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let accumulated = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (data === "[DONE]") break;
            try {
              const parsed = JSON.parse(data);
              if (parsed.text) {
                accumulated += parsed.text;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId
                      ? { ...m, content: accumulated, pending: false }
                      : m
                  )
                );
              }
            } catch {
              // ignore parse errors
            }
          }
        }

        // Ensure pending is cleared even if no content
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, pending: false } : m
          )
        );
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: "⚠️ Error connecting to the assistant. Please try again.",
                  pending: false,
                }
              : m
          )
        );
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [messages, isStreaming]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const handleSuggestion = (s: string) => {
    send(s);
  };

  const stopStreaming = () => {
    abortRef.current?.abort();
    setIsStreaming(false);
  };

  const showSuggestions =
    messages.length === 1 && messages[0].id === "welcome";

  return (
    <div className="chat-view">
      {/* Messages */}
      <div className="chat-messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-bubble chat-bubble--${msg.role}`}>
            {msg.role === "assistant" && (
              <span className="chat-avatar">✦</span>
            )}
            <div className="chat-bubble-content">
              {msg.pending ? (
                <span className="chat-typing">
                  <span /><span /><span />
                </span>
              ) : (
                <MessageContent content={msg.content} />
              )}
            </div>
          </div>
        ))}

        {/* Suggestions after welcome */}
        {showSuggestions && (
          <div className="chat-suggestions">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                className="chat-suggestion"
                onClick={() => handleSuggestion(s)}
                disabled={isStreaming}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="chat-input-area">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything about this system…"
          rows={2}
          disabled={isStreaming}
        />
        <button
          className={`chat-send ${isStreaming ? "chat-send--stop" : ""}`}
          onClick={isStreaming ? stopStreaming : () => send(input)}
          title={isStreaming ? "Stop" : "Send (Enter)"}
        >
          {isStreaming ? "■" : "▶"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MessageContent — renders markdown-ish text simply
// ---------------------------------------------------------------------------

function MessageContent({ content }: { content: string }) {
  // Simple renderer: bold, code, line breaks
  const lines = content.split("\n");
  return (
    <div className="chat-message-text">
      {lines.map((line, i) => {
        if (line.startsWith("# ")) return <h4 key={i}>{line.slice(2)}</h4>;
        if (line.startsWith("## ")) return <h5 key={i}>{line.slice(3)}</h5>;
        if (line.startsWith("• ") || line.startsWith("- ") || line.startsWith("* ")) {
          return <div key={i} className="chat-list-item">· {renderInline(line.slice(2))}</div>;
        }
        if (line.startsWith("```")) return null; // skip code fence lines
        return <p key={i}>{renderInline(line)}</p>;
      })}
    </div>
  );
}

function renderInline(text: string): React.ReactNode {
  // Bold: **text**
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={i}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}
