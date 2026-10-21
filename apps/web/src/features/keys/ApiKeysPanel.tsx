import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, type KeyIssuedOut, type KeyOut } from "@/api/client";
import { queryKeys, useKeys } from "@/api/queries";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { DataState } from "@/components/common/DataState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";

const KEY_STATUS_LABELS: Record<string, string> = {
  active: "Активен",
  revoked: "Отозван",
};

interface ApiKeysPanelProps {
  projectId: string;
}

export function ApiKeysPanel({ projectId }: ApiKeysPanelProps) {
  const queryClient = useQueryClient();
  const keys = useKeys(projectId);
  const [issued, setIssued] = useState<KeyIssuedOut | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<KeyOut | null>(null);

  const invalidateKeys = () => queryClient.invalidateQueries({ queryKey: queryKeys.keys(projectId) });

  const issueKey = useMutation({
    mutationFn: () => api.issueKey(projectId),
    onSuccess: (key) => setIssued(key),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: invalidateKeys,
  });

  const revokeKey = useMutation({
    mutationFn: (keyId: string) => api.revokeKey(projectId, keyId),
    onSuccess: () => {
      toast.success("Ключ отозван");
      setRevokeTarget(null);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: invalidateKeys,
  });

  function closeIssued() {
    setIssued(null);
  }

  async function copySecret() {
    if (!issued) {
      return;
    }
    try {
      await navigator.clipboard.writeText(issued.secret);
      toast.success("Ключ скопирован");
    } catch {
      toast.error("Не удалось скопировать ключ");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" onClick={() => issueKey.mutate()} disabled={issueKey.isPending}>
          {issueKey.isPending ? "Создание…" : "Создать ключ"}
        </Button>
      </div>

      <DataState
        isPending={keys.isPending}
        error={keys.error}
        isEmpty={keys.data?.length === 0}
        empty={<p className="text-sm text-muted-foreground">API-ключей пока нет</p>}
        onRetry={() => void keys.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Префикс</TableHead>
              <TableHead>Статус</TableHead>
              <TableHead>Создан</TableHead>
              <TableHead className="text-right">Действия</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.data?.map((key) => (
              <TableRow key={key.id}>
                <TableCell className="font-mono text-xs">{key.prefix}</TableCell>
                <TableCell>
                  <Badge variant={key.status === "revoked" ? "outline" : "secondary"}>
                    {KEY_STATUS_LABELS[key.status] ?? key.status}
                  </Badge>
                </TableCell>
                <TableCell>{formatDateTime(key.created_at)}</TableCell>
                <TableCell className="text-right">
                  {key.status === "active" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => setRevokeTarget(key)}
                    >
                      Отозвать
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataState>

      <Dialog open={issued !== null} onOpenChange={(open) => !open && closeIssued()}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Новый API-ключ</DialogTitle>
            <DialogDescription>Секрет показывается один раз. Сохраните его сейчас.</DialogDescription>
          </DialogHeader>
          <Alert>
            <AlertDescription>Повторно просмотреть секрет будет невозможно.</AlertDescription>
          </Alert>
          <Input readOnly value={issued?.secret ?? ""} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
          <DialogFooter>
            <Button variant="outline" onClick={() => void copySecret()}>
              Копировать
            </Button>
            <Button onClick={closeIssued}>Готово</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => !open && setRevokeTarget(null)}
        title="Отозвать ключ?"
        description={`Ключ ${revokeTarget?.prefix ?? ""} перестанет работать. Это действие необратимо.`}
        confirmLabel="Отозвать"
        destructive
        pending={revokeKey.isPending}
        onConfirm={async () => {
          if (revokeTarget) {
            await revokeKey.mutateAsync(revokeTarget.id);
          }
        }}
      />
    </div>
  );
}
