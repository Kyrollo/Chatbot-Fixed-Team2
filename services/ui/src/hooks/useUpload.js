import { useState, useCallback } from "react";
import { ingestionApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import toast from "react-hot-toast";

const POLL_INTERVAL = 2000;
const MAX_POLLS     = 60; // 2 min max

export function useUpload(domainId) {
  const { user } = useAuth();
  const [uploads, setUploads] = useState([]); // [{ id, filename, status, error }]

  const upsert = (id, patch) =>
    setUploads((u) => {
      const idx = u.findIndex((x) => x.id === id);
      if (idx === -1) return u;
      return [...u.slice(0, idx), { ...u[idx], ...patch }, ...u.slice(idx + 1)];
    });

  const upload = useCallback(
    async (file) => {
      const temp = { id: `tmp-${Date.now()}`, filename: file.name, status: "uploading" };
      setUploads((u) => [...u, temp]);

      try {
        const { document_id } = await ingestionApi.upload(user.token, file, domainId);

        // Replace temp entry with real id
        setUploads((u) =>
          u.map((x) =>
            x.id === temp.id
              ? { id: document_id, filename: file.name, status: "pending" }
              : x,
          ),
        );

        // Poll until done or failed
        let polls = 0;
        const interval = setInterval(async () => {
          polls++;
          try {
            const data = await ingestionApi.getStatus(user.token, document_id);
            upsert(document_id, { status: data.status, error: data.error_msg });

            if (data.status === "done") {
              clearInterval(interval);
              toast.success(`"${file.name}" indexed successfully.`);
            } else if (data.status === "failed") {
              clearInterval(interval);
              toast.error(`Indexing failed: ${data.error_msg || "unknown error"}`);
            } else if (polls >= MAX_POLLS) {
              clearInterval(interval);
              upsert(document_id, { status: "timeout" });
            }
          } catch {
            clearInterval(interval);
          }
        }, POLL_INTERVAL);
      } catch (err) {
        setUploads((u) => u.filter((x) => x.id !== temp.id));
        toast.error(`Upload failed: ${err.detail || err.message}`);
      }
    },
    [domainId, user],
  );

  const dismiss = (id) => setUploads((u) => u.filter((x) => x.id !== id));

  return { uploads, upload, dismiss };
}
