import { useState } from "react";
import { Link, useParams } from "react-router";
import { useProject } from "@/api/queries";
import { DataState } from "@/components/common/DataState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AddSourceDialog } from "@/features/knowledge/AddSourceDialog";
import { SourcesList } from "@/features/knowledge/SourcesList";
import { ApiKeysPanel } from "@/features/keys/ApiKeysPanel";
import { SearchPanel } from "@/features/search/SearchPanel";

export function KnowledgePage() {
  const { projectId = "" } = useParams();
  const project = useProject(projectId);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <DataState
      isPending={project.isPending}
      error={project.error}
      isEmpty={false}
      empty={null}
      onRetry={() => void project.refetch()}
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <Button asChild variant="ghost" size="sm" className="-ml-3 mb-1">
              <Link to="/projects">← Проекты</Link>
            </Button>
            <h1 className="text-2xl font-semibold tracking-tight">{project.data?.name}</h1>
            <p className="text-sm text-muted-foreground">База знаний проекта</p>
          </div>
          <Button onClick={() => setAddOpen(true)}>Добавить источник</Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Источники</CardTitle>
            <CardDescription>Текст, файлы и ссылки, которые индексируются для поиска</CardDescription>
          </CardHeader>
          <CardContent>
            <SourcesList projectId={projectId} />
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>API-ключи</CardTitle>
              <CardDescription>Ключи для доступа к поиску через /api/v1</CardDescription>
            </CardHeader>
            <CardContent>
              <ApiKeysPanel projectId={projectId} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Тестовый поиск</CardTitle>
              <CardDescription>Проверка результатов поиска по источникам проекта</CardDescription>
            </CardHeader>
            <CardContent>
              <SearchPanel projectId={projectId} />
            </CardContent>
          </Card>
        </div>

        <AddSourceDialog projectId={projectId} open={addOpen} onOpenChange={setAddOpen} />
      </div>
    </DataState>
  );
}
