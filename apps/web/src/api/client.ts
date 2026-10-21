import { clearSession, getToken } from "@/api/auth";

const API_PREFIX = "/api";
const LOGIN_PATH = "/auth/login";

export const UNAUTHORIZED_EVENT = "vector:unauthorized";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export interface LoginIn {
  email: string;
  password: string;
}

export interface LoginOut {
  token: string;
  expires_at: string;
}

export interface ProjectOut {
  id: string;
  name: string;
  created_at: string;
}

export interface KeyIssuedOut {
  id: string;
  prefix: string;
  secret: string;
  created_at: string;
}

export interface KeyOut {
  id: string;
  prefix: string;
  status: string;
  created_at: string;
  revoked_at: string | null;
}

export interface SourceOut {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  status: "pending" | "indexed" | "failed";
  current_version: number | null;
  origin_key: string | null;
  content_type: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface VersionOut {
  id: string;
  version: number;
  status: "pending" | "published" | "failed";
  content_hash: string | null;
  extracted_chars: number | null;
  vector_indexed: boolean;
  error_message: string | null;
  created_at: string;
}

export interface JobOut {
  id: string;
  type: string;
  status: string;
}

export interface SourceEditIn {
  title?: string;
  text?: string;
}

export interface SearchIn {
  project_id: string;
  query: string;
  limit: number;
}

export interface SearchHit {
  source_id: string;
  title: string;
  version: number;
  text: string;
  score: number;
  locator: Record<string, unknown>;
}

export interface SearchOut {
  hits: SearchHit[];
  warning: string | null;
}

interface RequestOptions {
  body?: unknown;
  form?: FormData;
}

const VALIDATION_MESSAGE = "Проверьте введённые данные";
const HTTP_MESSAGE = "Не удалось выполнить запрос";

function parseError(status: number, data: unknown): ApiError {
  const detail = (data as { detail?: unknown } | undefined)?.detail;
  if (detail !== null && typeof detail === "object" && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code === "string") {
      return new ApiError(status, code, typeof message === "string" ? message : HTTP_MESSAGE);
    }
  }
  if (Array.isArray(detail)) {
    return new ApiError(status, "validation_error", VALIDATION_MESSAGE);
  }
  return new ApiError(status, "http_error", HTTP_MESSAGE);
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(`${API_PREFIX}${path}`, { method, headers, body });

  if (response.status === 401 && path !== LOGIN_PATH) {
    clearSession();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = undefined;
  }

  if (!response.ok) {
    throw parseError(response.status, data);
  }
  return data as T;
}

export const api = {
  login: (body: LoginIn) => request<LoginOut>("POST", LOGIN_PATH, { body }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<{ role: string }>("GET", "/auth/me"),

  listProjects: () => request<ProjectOut[]>("GET", "/projects"),
  createProject: (name: string) => request<ProjectOut>("POST", "/projects", { body: { name } }),
  getProject: (projectId: string) => request<ProjectOut>("GET", `/projects/${projectId}`),
  renameProject: (projectId: string, name: string) =>
    request<ProjectOut>("PATCH", `/projects/${projectId}`, { body: { name } }),
  deleteProject: (projectId: string) => request<void>("DELETE", `/projects/${projectId}`),

  listKeys: (projectId: string) => request<KeyOut[]>("GET", `/projects/${projectId}/keys`),
  issueKey: (projectId: string) => request<KeyIssuedOut>("POST", `/projects/${projectId}/keys`),
  revokeKey: (projectId: string, keyId: string) =>
    request<KeyOut>("POST", `/projects/${projectId}/keys/${keyId}/revoke`),

  listSources: (projectId: string) => request<SourceOut[]>("GET", `/projects/${projectId}/sources`),
  addTextSource: (projectId: string, body: { title: string; text: string }) =>
    request<SourceOut>("POST", `/projects/${projectId}/sources/text`, { body }),
  addFileSource: (projectId: string, file: File, title?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title) {
      form.append("title", title);
    }
    return request<SourceOut>("POST", `/projects/${projectId}/sources/file`, { form });
  },
  addUrlSource: (projectId: string, body: { url: string; title?: string }) =>
    request<SourceOut>("POST", `/projects/${projectId}/sources/url`, { body }),
  editSource: (projectId: string, sourceId: string, body: SourceEditIn) =>
    request<SourceOut>("PATCH", `/projects/${projectId}/sources/${sourceId}`, { body }),
  deleteSource: (projectId: string, sourceId: string) =>
    request<void>("DELETE", `/projects/${projectId}/sources/${sourceId}`),
  listVersions: (projectId: string, sourceId: string) =>
    request<VersionOut[]>("GET", `/projects/${projectId}/sources/${sourceId}/versions`),
  reindexSource: (projectId: string, sourceId: string) =>
    request<JobOut>("POST", `/projects/${projectId}/sources/${sourceId}/reindex`),

  search: (body: SearchIn) => request<SearchOut>("POST", "/v1/search", { body }),
};
