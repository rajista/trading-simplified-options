async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

export const api = {
  health: () => request("/api/health"),
  expiries: (refresh = false) =>
    request(`/api/expiries${refresh ? "?refresh=true" : ""}`),
  chain: (expiry, refresh = false) =>
    request(`/api/chain?expiry=${encodeURIComponent(expiry)}${refresh ? "&refresh=true" : ""}`),
  marketContext: (refresh = false) =>
    request(`/api/market-context${refresh ? "?refresh=true" : ""}`),
  recommendations: (payload) =>
    request("/api/recommendations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  strategy: (type, expiry, farExpiry) => {
    const params = new URLSearchParams({ expiry });
    if (farExpiry) params.set("far_expiry", farExpiry);
    return request(`/api/strategies/${type}?${params}`);
  },
  coveredCall: (payload) =>
    request("/api/strategies/covered-call", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  portfolioStrategy: (payload) =>
    request("/api/strategies/portfolio", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  analysis: (payload) =>
    request("/api/analysis", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  report: (payload) =>
    request("/api/reports", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
