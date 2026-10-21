import { QueryClient, useQuery } from "@tanstack/react-query";
import { ApiError, api } from "@/api/client";

export const queryKeys = {
  projects: ["projects"] as const,
  project: (projectId: string) => ["projects", projectId] as const,
  sources: (projectId: string) => ["sources", projectId] as const,
  versions: (projectId: string, sourceId: string) => ["versions", projectId, sourceId] as const,
  keys: (projectId: string) => ["keys", projectId] as const,
};

const PENDING_POLL_MS = 3000;

function isClientError(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        retry: (failureCount, error) => !isClientError(error) && failureCount < 2,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: api.listProjects,
  });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => api.getProject(projectId),
  });
}

export function useSources(projectId: string) {
  return useQuery({
    queryKey: queryKeys.sources(projectId),
    queryFn: () => api.listSources(projectId),
    refetchInterval: (query) =>
      query.state.data?.some((source) => source.status === "pending") ? PENDING_POLL_MS : false,
  });
}

export function useVersions(projectId: string, sourceId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.versions(projectId, sourceId),
    queryFn: () => api.listVersions(projectId, sourceId),
    enabled,
  });
}

export function useKeys(projectId: string) {
  return useQuery({
    queryKey: queryKeys.keys(projectId),
    queryFn: () => api.listKeys(projectId),
  });
}
