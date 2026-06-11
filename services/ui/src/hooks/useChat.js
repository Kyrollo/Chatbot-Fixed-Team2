/**
 * useChat — manages conversation messages, streaming, and citations for one domain.
 *
 * Messages shape:
 *   { id, role: "user"|"assistant", content, citations?, loading?, error? }
 */
import { useState, useRef, useCallback } from "react";
import { generationApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import toast from "react-hot-toast";

export function useChat(domainId) {
  const { user } = useAuth();
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming]   = useState(false);
  const abortRef = useRef(null);
  const idCounter = useRef(0);

  const nextId = () => `msg-${++idCounter.current}`;

  const appendMessage = (msg) =>
    setMessages((prev) => [...prev, msg]);

  const patchLast = (updater) =>
    setMessages((prev) => {
      if (!prev.length) return prev;
      const last = prev[prev.length - 1];
      return [...prev.slice(0, -1), { ...last, ...updater(last) }];
    });

  const send = useCallback(
    async (query, { stream = true } = {}) => {
      if (!query.trim() || !domainId) return;

      // Add user message
      appendMessage({ id: nextId(), role: "user", content: query });

      const assistantId = nextId();
      appendMessage({ id: assistantId, role: "assistant", content: "", loading: true });
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        if (stream) {
          const reader = await generationApi.queryStream(
            user.token,
            { query, domain_id: domainId },
            controller.signal,
          );
          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const current = buffer;
            patchLast((msg) => ({
              ...msg,
              content: current,
              loading: false,
            }));
          }

          // Stream complete — no citation payload from streaming endpoint.
          // Citations arrive via the non-stream fallback below if needed.
          patchLast((msg) => ({ ...msg, loading: false }));
        } else {
          const response = await generationApi.query(user.token, {
            query,
            domain_id: domainId,
          });
          patchLast(() => ({
            id: assistantId,
            role: "assistant",
            content: response.answer,
            citations: response.citations,
            llm_route: response.llm_route,
            model: response.model,
            cache_hit: response.cache_hit,
            loading: false,
          }));
        }
      } catch (err) {
        if (err.name === "AbortError") {
          patchLast(() => ({ loading: false, error: "Cancelled." }));
          return;
        }
        const msg = err.detail || err.message || "Unknown error";
        toast.error(msg);
        patchLast(() => ({ loading: false, error: msg }));
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [domainId, user],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
  }, []);

  return { messages, streaming, send, cancel, clear };
}
