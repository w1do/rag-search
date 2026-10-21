import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, type SourceOut } from "@/api/client";
import { queryKeys } from "@/api/queries";
import { getErrorMessage } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface EditSourceDialogProps {
  projectId: string;
  source: SourceOut | null;
  onClose: () => void;
}

interface EditFormProps {
  projectId: string;
  source: SourceOut;
  onClose: () => void;
}

function EditForm({ projectId, source, onClose }: EditFormProps) {
  const queryClient = useQueryClient();
  const isText = source.kind === "text";
  const [title, setTitle] = useState(source.title);
  const [text, setText] = useState("");

  const editSource = useMutation({
    mutationFn: () => {
      const body: { title?: string; text?: string } = {};
      if (title.trim() !== source.title) {
        body.title = title.trim();
      }
      if (isText && text.trim() !== "") {
        body.text = text;
      }
      return api.editSource(projectId, source.id, body);
    },
    onSuccess: () => {
      toast.success("Источник сохранён");
      onClose();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.sources(projectId) }),
  });

  const hasChanges = title.trim() !== source.title || (isText && text.trim() !== "");
  const canSubmit = title.trim() !== "" && hasChanges;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) {
      editSource.mutate();
    }
  }

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label htmlFor="edit-source-title">Название</Label>
        <Input id="edit-source-title" required value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      {isText && (
        <div className="space-y-2">
          <Label htmlFor="edit-source-text">Новый текст (создаст новую версию)</Label>
          <Textarea
            id="edit-source-text"
            rows={8}
            placeholder="Оставьте пустым, чтобы не менять текст"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}
      <DialogFooter>
        <Button type="submit" disabled={!canSubmit || editSource.isPending}>
          {editSource.isPending ? "Сохранение…" : "Сохранить"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function EditSourceDialog({ projectId, source, onClose }: EditSourceDialogProps) {
  return (
    <Dialog open={source !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Редактировать источник</DialogTitle>
        </DialogHeader>
        {source && <EditForm key={source.id} projectId={projectId} source={source} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  );
}
