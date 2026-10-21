import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type SearchIn } from "@/api/client";
import { DataState } from "@/components/common/DataState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const DEFAULT_LIMIT = 5;
const MIN_LIMIT = 1;
const MAX_LIMIT = 50;

function formatLocator(locator: Record<string, unknown>): string {
  return Object.entries(locator)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(", ");
}

interface SearchPanelProps {
  projectId: string;
}

export function SearchPanel({ projectId }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);

  const search = useMutation({
    mutationFn: (body: SearchIn) => api.search(body),
  });

  const trimmed = query.trim();
  const limitValid = Number.isInteger(limit) && limit >= MIN_LIMIT && limit <= MAX_LIMIT;

  function runSearch() {
    search.mutate({ project_id: projectId, query: trimmed, limit });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmed && limitValid) {
      runSearch();
    }
  }

  return (
    <div className="space-y-4">
      <form className="space-y-4" onSubmit={handleSubmit}>
        <div className="space-y-2">
          <Label htmlFor="search-query">Запрос</Label>
          <Input id="search-query" required value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="search-limit">Результатов</Label>
            <Input
              id="search-limit"
              type="number"
              min={MIN_LIMIT}
              max={MAX_LIMIT}
              className="w-28"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
          </div>
          <Button type="submit" disabled={!trimmed || !limitValid || search.isPending}>
            {search.isPending ? "Поиск…" : "Искать"}
          </Button>
        </div>
      </form>

      {search.data?.warning && (
        <Alert>
          <AlertDescription>{search.data.warning}</AlertDescription>
        </Alert>
      )}

      {search.isIdle ? (
        <p className="text-sm text-muted-foreground">Введите запрос, чтобы проверить поиск по источникам проекта</p>
      ) : (
        <DataState
          isPending={search.isPending}
          error={search.error}
          isEmpty={search.data?.hits.length === 0}
          empty={<p className="text-sm text-muted-foreground">По запросу ничего не найдено</p>}
          onRetry={() => search.variables && runSearch()}
        >
          <ul className="space-y-3">
            {search.data?.hits.map((hit, index) => (
              <li key={`${hit.source_id}-${index}`} className="rounded-lg border border-border p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="font-medium">{hit.title}</span>
                  <Badge variant="outline">v{hit.version}</Badge>
                  <span className="text-xs text-muted-foreground">score {hit.score.toFixed(3)}</span>
                </div>
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{hit.text}</p>
                {Object.keys(hit.locator).length > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">{formatLocator(hit.locator)}</p>
                )}
              </li>
            ))}
          </ul>
        </DataState>
      )}
    </div>
  );
}
