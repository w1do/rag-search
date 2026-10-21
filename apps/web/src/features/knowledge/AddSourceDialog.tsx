import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/api/client";
import { queryKeys } from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { getErrorMessage } from "@/lib/errors";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const FILE_ACCEPT = ".txt,.md,.pdf,.docx";
const LINK_NOT_CONFIGURED = "link_ingestion_not_configured";

type SourceKind = "text" | "file" | "url";

interface AddSourceDialogProps {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function uploadErrorMessage(error: unknown): string {
  const status = (error as { status?: number }).status;
  if (status === 413) {
    return "Файл больше 10 МБ";
  }
  if (status === 422) {
    return "Формат файла не поддерживается. Допустимы TXT, MD, PDF и DOCX";
  }
  return getErrorMessage(error);
}

function linkErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === LINK_NOT_CONFIGURED) {
    return "Добавление ссылок недоступно: на сервере не задан FIRECRAWL_API_KEY";
  }
  if (error instanceof ApiError && error.status === 422) {
    return "Поддерживаются только ссылки http и https";
  }
  return getErrorMessage(error);
}

export function AddSourceDialog({ projectId, open, onOpenChange }: AddSourceDialogProps) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<SourceKind>("text");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);

  const addSource = useMutation({
    mutationFn: () => {
      if (kind === "file" && file) {
        return api.addFileSource(projectId, file, title.trim() || undefined);
      }
      if (kind === "url") {
        return api.addUrlSource(projectId, { url: url.trim(), title: title.trim() || undefined });
      }
      return api.addTextSource(projectId, { title: title.trim(), text });
    },
    onSuccess: () => {
      toast.success("Источник добавлен");
      reset();
      onOpenChange(false);
    },
    onError: (error) =>
      toast.error(kind === "url" ? linkErrorMessage(error) : uploadErrorMessage(error)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.sources(projectId) }),
  });

  function reset() {
    setTitle("");
    setText("");
    setFile(null);
    setUrl("");
    setFileError(null);
  }

  function handleFileChange(selected: File | null) {
    setFileError(null);
    if (selected && selected.size > MAX_FILE_BYTES) {
      setFile(null);
      setFileError("Файл больше 10 МБ");
      return;
    }
    setFile(selected);
  }

  const canSubmit =
    kind === "text"
      ? title.trim() !== "" && text.trim() !== ""
      : kind === "url"
        ? url.trim() !== ""
        : file !== null;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) {
      addSource.mutate();
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset();
        }
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Добавить источник</DialogTitle>
        </DialogHeader>
        <Tabs value={kind} onValueChange={(value) => setKind(value as SourceKind)}>
          <TabsList className="w-full">
            <TabsTrigger value="text">Текст</TabsTrigger>
            <TabsTrigger value="file">Файл</TabsTrigger>
            <TabsTrigger value="url">Ссылка</TabsTrigger>
          </TabsList>
          <form className="mt-4 space-y-4" onSubmit={handleSubmit}>
            <TabsContent value="text" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="source-title">Название</Label>
                <Input id="source-title" required value={title} onChange={(e) => setTitle(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="source-text">Текст</Label>
                <Textarea id="source-text" rows={8} required value={text} onChange={(e) => setText(e.target.value)} />
              </div>
            </TabsContent>
            <TabsContent value="file" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="source-file">Файл (до 10 МБ)</Label>
                <Input
                  id="source-file"
                  type="file"
                  accept={FILE_ACCEPT}
                  onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="source-file-title">Название (необязательно)</Label>
                <Input id="source-file-title" value={title} onChange={(e) => setTitle(e.target.value)} />
              </div>
              {fileError && (
                <Alert variant="destructive">
                  <AlertDescription>{fileError}</AlertDescription>
                </Alert>
              )}
            </TabsContent>
            <TabsContent value="url" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="source-url">Ссылка</Label>
                <Input id="source-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="source-url-title">Название (необязательно)</Label>
                <Input id="source-url-title" value={title} onChange={(e) => setTitle(e.target.value)} />
              </div>
            </TabsContent>
            <DialogFooter>
              <Button type="submit" disabled={!canSubmit || addSource.isPending}>
                {addSource.isPending ? "Добавление…" : "Добавить"}
              </Button>
            </DialogFooter>
          </form>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
