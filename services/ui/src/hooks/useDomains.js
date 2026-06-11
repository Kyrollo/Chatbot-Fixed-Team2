import { useState, useEffect, useCallback } from "react";
import { domainsApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import toast from "react-hot-toast";

export function useDomains() {
  const { user } = useAuth();
  const [domains, setDomains] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchDomains = useCallback(async () => {
    if (!user) return;
    try {
      setLoading(true);
      const data = await domainsApi.list(user.token);
      setDomains(data);
    } catch (err) {
      toast.error(`Failed to load domains: ${err.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => { fetchDomains(); }, [fetchDomains]);

  const createDomain = async (payload) => {
    const newDomain = await domainsApi.create(user.token, payload);
    setDomains((d) => [...d, newDomain]);
    return newDomain;
  };

  return { domains, loading, refetch: fetchDomains, createDomain };
}
