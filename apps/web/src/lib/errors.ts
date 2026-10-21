import { ApiError } from "@/api/client";

export function getErrorMessage(error: unknown, fallback = "Произошла ошибка"): string {
  return error instanceof ApiError ? error.message : fallback;
}
