import { Link } from "react-router";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-4 p-4 text-center">
      <p className="text-5xl font-semibold tracking-tight">404</p>
      <p className="text-muted-foreground">Страница не найдена</p>
      <Button asChild variant="outline">
        <Link to="/projects">К проектам</Link>
      </Button>
    </div>
  );
}
