import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { saveSession } from "@/api/auth";
import { ApiError, api } from "@/api/client";
import { getErrorMessage } from "@/lib/errors";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Неверный email или пароль";
    }
    if (error.code === "auth_not_configured") {
      return "Вход не настроен: заполните EMAIL/PASSWORD в .env";
    }
    if (error.code === "db_unavailable") {
      return "База данных недоступна";
    }
  }
  return getErrorMessage(error);
}

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () => api.login({ email, password }),
    onSuccess: (session) => {
      saveSession(session.token, session.expires_at);
      navigate("/projects", { replace: true });
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate();
  }

  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Вход в Vector</CardTitle>
          <CardDescription>Войдите с учётными данными владельца</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Пароль</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {login.isError && (
              <Alert variant="destructive">
                <AlertDescription>{loginErrorMessage(login.error)}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" className="w-full" disabled={login.isPending}>
              {login.isPending ? "Вход…" : "Войти"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
