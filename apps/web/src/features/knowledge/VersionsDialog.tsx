import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DataState } from "@/components/common/DataState";
import { useVersions } from "@/api/queries";
import { formatDateTime } from "@/lib/format";
import type { VersionOut } from "@/api/client";

const VERSION_STATUS_LABELS: Record<VersionOut["status"], string> = {
  pending: "В обработке",
  published: "Опубликована",
  failed: "Ошибка",
};

interface VersionsDialogProps {
  projectId: string;
  sourceId: string | null;
  sourceTitle: string;
  onClose: () => void;
}

export function VersionsDialog({ projectId, sourceId, sourceTitle, onClose }: VersionsDialogProps) {
  const versions = useVersions(projectId, sourceId ?? "", sourceId !== null);

  return (
    <Dialog open={sourceId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Версии: {sourceTitle}</DialogTitle>
          <DialogDescription>История версий источника и состояние индексации</DialogDescription>
        </DialogHeader>
        <DataState
          isPending={versions.isPending}
          error={versions.error}
          isEmpty={versions.data?.length === 0}
          empty={<p className="text-sm text-muted-foreground">Версий пока нет</p>}
          onRetry={() => void versions.refetch()}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Версия</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Символов</TableHead>
                <TableHead>Индекс</TableHead>
                <TableHead>Создана</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.data?.map((version) => (
                <TableRow key={version.id}>
                  <TableCell>v{version.version}</TableCell>
                  <TableCell>
                    <Badge variant={version.status === "failed" ? "destructive" : "secondary"}>
                      {VERSION_STATUS_LABELS[version.status]}
                    </Badge>
                    {version.error_message && (
                      <p className="mt-1 text-xs text-destructive">{version.error_message}</p>
                    )}
                  </TableCell>
                  <TableCell>{version.extracted_chars ?? "—"}</TableCell>
                  <TableCell>{version.vector_indexed ? "Готов" : "Нет"}</TableCell>
                  <TableCell>{formatDateTime(version.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataState>
      </DialogContent>
    </Dialog>
  );
}
