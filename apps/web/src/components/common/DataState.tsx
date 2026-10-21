import type { ReactNode } from "react";
import { getErrorMessage } from "@/lib/errors";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface DataStateProps {
  isPending: boolean;
  error: unknown;
  isEmpty: boolean;
  empty: ReactNode;
  onRetry: () => void;
  children: ReactNode;
}

export function DataState({ isPending, error, isEmpty, empty, onRetry, children }: DataStateProps) {
  if (isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-3/4" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Не удалось загрузить данные</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>{getErrorMessage(error)}</p>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Повторить
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (isEmpty) {
    return <>{empty}</>;
  }

  return <>{children}</>;
}
