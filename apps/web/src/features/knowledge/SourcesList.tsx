import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, type SourceOut } from "@/api/client";
import { queryKeys, useSources } from "@/api/queries";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { DataState } from "@/components/common/DataState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EditSourceDialog } from "@/features/knowledge/EditSourceDialog";
import { VersionsDialog } from "@/features/knowledge/VersionsDialog";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";

const SOURCE_STATUS_LABELS: Record<SourceOut["status"], string> = {
  pending: "В обработке",
  indexed: "Проиндексирован",
  failed: "Ошибка",
};

const SOURCE_KIND_LABELS: Record<string, string> = {
  text: "Текст",
  file: "Файл",
  url: "Ссылка",
};

function statusVariant(status: SourceOut["status"]): "default" | "secondary" | "destructive" {
  if (status === "failed") {
    return "destructive";
  }
  return status === "indexed" ? "default" : "secondary";
}

interface SourcesListProps {
  projectId: string;
}

export function SourcesList({ projectId }: SourcesListProps) {
  const queryClient = useQueryClient();
  const sources = useSources(projectId);
  const [editTarget, setEditTarget] = useState<SourceOut | null>(null);
  const [versionsTarget, setVersionsTarget] = useState<SourceOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SourceOut | null>(null);

  const deleteSource = useMutation({
    mutationFn: (sourceId: string) => api.deleteSource(projectId, sourceId),
    onSuccess: () => {
      toast.success("Источник удалён");
      setDeleteTarget(null);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.sources(projectId) }),
  });

  return (
    <>
      <DataState
        isPending={sources.isPending}
        error={sources.error}
        isEmpty={sources.data?.length === 0}
        empty={<p className="text-sm text-muted-foreground">Источников пока нет. Добавьте текст, файл или ссылку.</p>}
        onRetry={() => void sources.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Название</TableHead>
              <TableHead>Тип</TableHead>
              <TableHead>Статус</TableHead>
              <TableHead>Версия</TableHead>
              <TableHead>Обновлён</TableHead>
              <TableHead className="text-right">Действия</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sources.data?.map((source) => (
              <TableRow key={source.id}>
                <TableCell className="font-medium">{source.title}</TableCell>
                <TableCell>{SOURCE_KIND_LABELS[source.kind] ?? source.kind}</TableCell>
                <TableCell>
                  <Badge variant={statusVariant(source.status)}>{SOURCE_STATUS_LABELS[source.status]}</Badge>
                  {source.error_message && (
                    <p className="mt-1 max-w-xs text-xs text-destructive">{source.error_message}</p>
                  )}
                </TableCell>
                <TableCell>{source.current_version ? `v${source.current_version}` : "—"}</TableCell>
                <TableCell>{formatDateTime(source.updated_at)}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => setVersionsTarget(source)}>
                      Версии
                    </Button>
                    {source.kind === "text" && (
                      <Button variant="outline" size="sm" onClick={() => setEditTarget(source)}>
                        Изменить
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => setDeleteTarget(source)}
                    >
                      Удалить
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataState>

      <EditSourceDialog projectId={projectId} source={editTarget} onClose={() => setEditTarget(null)} />

      <VersionsDialog
        projectId={projectId}
        sourceId={versionsTarget?.id ?? null}
        sourceTitle={versionsTarget?.title ?? ""}
        onClose={() => setVersionsTarget(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Удалить источник?"
        description={`Источник «${deleteTarget?.title ?? ""}» и все его версии будут удалены без возможности восстановления.`}
        confirmLabel="Удалить"
        destructive
        pending={deleteSource.isPending}
        onConfirm={async () => {
          if (deleteTarget) {
            await deleteSource.mutateAsync(deleteTarget.id);
          }
        }}
      />
    </>
  );
}
