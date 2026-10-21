import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { clearSession } from "@/api/auth";
import { api, UNAUTHORIZED_EVENT } from "@/api/client";
import { Button } from "@/components/ui/button";

export function AppShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    function handleUnauthorized() {
      queryClient.clear();
      navigate("/login", { replace: true });
    }
    window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
  }, [navigate, queryClient]);

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      // The local session is cleared even if the server call fails.
    }
    clearSession();
    queryClient.clear();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <div className="flex items-center gap-6">
            <span className="font-semibold tracking-tight">Vector</span>
            <nav className="flex items-center gap-4 text-sm">
              <NavLink
                to="/projects"
                className={({ isActive }) =>
                  isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground"
                }
              >
                Проекты
              </NavLink>
            </nav>
          </div>
          <Button variant="ghost" size="sm" onClick={handleLogout}>
            Выйти
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
