function getDefaultApiBase(): string {
  // Production is served by FastAPI and Vite development uses its /api proxy.
  // Keeping calls same-origin avoids baking the legacy 8080 port into an A2
  // deployment that runs the supervisor on 8070.
  return '';
}

export const API_BASE = import.meta.env.VITE_API_BASE ?? getDefaultApiBase();
