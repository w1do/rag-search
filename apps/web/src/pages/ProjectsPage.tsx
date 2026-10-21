import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, type ProjectOut } from "@/api/client";
import { queryKeys, useProjects } from "@/api/queries";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { DataState } from "@/components/common/DataState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/format";
import { getErrorMessage } from "@/lib/errors";

type FormMode = "create" | "rename";

interface ProjectFormProps {
  initialName: string;
  submitLabel: string;
  pending: boolean;
  onSubmit: (name: string) => void;
}

function ProjectForm({ initialName, submitLabel, pending, onSubmit }: ProjectFormProps) {
  const [name, setName] = useState(initialName);
  const trimmed = name.trim();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmed) {
      onSubmit(trimmed);
    }
  }

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label htmlFor="project-name">Название</Label>
        <Input
          id="project-name"
          autoFocus
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <DialogFooter>
        <Button type="submit" disabled={pending || !trimmed}>
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const projects = useProjects();
  const [formMode, setFormMode] = useState<FormMode | null>(null);
  const [selected, setSelected] = useState<ProjectOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectOut | null>(null);

  function closeForm() {
    setFormMode(null);
    setSelected(null);
  }

  function openCreate() {
    setSelected(null);
    setFormMode("create");
  }

  function openRename(project: ProjectOut) {
    setSelected(project);
    setFormMode("rename");
  }

  const invalidateProjects = () => queryClient.invalidateQueries({ queryKey: queryKeys.projects });

  const createProject = useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: (project) => {
      toast.success(`Проект «${project.name}» создан`);
      closeForm();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: invalidateProjects,
  });

  const renameProject = useMutation({
    mutationFn: ({ projectId, name }: { projectId: string; name: string }) =>
      api.renameProject(projectId, name),
    onSuccess: (project) => {
      toast.success(`Проект переименован в «${project.name}»`);
      closeForm();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: invalidateProjects,
  });

  const deleteProject = useMutation({
    mutationFn: (projectId: string) => api.deleteProject(projectId),
    onSuccess: () => {
      toast.success("Проект удалён");
      setDeleteTarget(null);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
    onSettled: invalidateProjects,
  });

  function handleFormSubmit(name: string) {
    if (formMode === "rename" && selected) {
      renameProject.mutate({ projectId: selected.id, name });
    } else {
      createProject.mutate(name);
    }
  }

  const formPending = createProject.isPending || renameProject.isPending;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Проекты</h1>
          <p className="text-sm text-muted-foreground">Базы знаний и доступ к ним</p>
        </div>
        <Button onClick={openCreate}>Создать проект</Button>
      </div>

      <DataState
        isPending={projects.isPending}
        error={projects.error}
        isEmpty={projects.data?.length === 0}
        empty={<p className="text-sm text-muted-foreground">Проектов пока нет. Создайте первый проект.</p>}
        onRetry={() => void projects.refetch()}
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data?.map((project) => (
            <Card key={project.id}>
              <CardHeader>
                <CardTitle>{project.name}</CardTitle>
                <CardDescription>Создан {formatDateTime(project.created_at)}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Button asChild size="sm">
                  <Link to={`/projects/${project.id}`}>Открыть</Link>
                </Button>
                <Button variant="outline" size="sm" onClick={() => openRename(project)}>
                  Переименовать
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive"
                  onClick={() => setDeleteTarget(project)}
                >
                  Удалить
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </DataState>

      <Dialog open={formMode !== null} onOpenChange={(open) => !open && closeForm()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{formMode === "rename" ? "Переименовать проект" : "Новый проект"}</DialogTitle>
          </DialogHeader>
          <ProjectForm
            initialName={formMode === "rename" ? (selected?.name ?? "") : ""}
            submitLabel={formMode === "rename" ? "Сохранить" : "Создать"}
            pending={formPending}
            onSubmit={handleFormSubmit}
          />
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Удалить проект?"
        description={`Проект «${deleteTarget?.name ?? ""}» и все его источники будут удалены без возможности восстановления.`}
        confirmLabel="Удалить"
        destructive
        pending={deleteProject.isPending}
        onConfirm={async () => {
          if (deleteTarget) {
            await deleteProject.mutateAsync(deleteTarget.id);
          }
        }}
      />
    </div>
  );
}
