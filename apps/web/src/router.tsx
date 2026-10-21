import { Navigate, createBrowserRouter, redirect } from "react-router";
import { isAuthenticated } from "@/api/auth";
import { AppShell } from "@/components/layout/AppShell";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ProjectsPage } from "@/pages/ProjectsPage";

function requireAuth() {
  if (!isAuthenticated()) {
    throw redirect("/login");
  }
  return null;
}

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    element: <AppShell />,
    loader: requireAuth,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
      { path: "projects", element: <ProjectsPage /> },
      { path: "projects/:projectId", element: <KnowledgePage /> },
    ],
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);
