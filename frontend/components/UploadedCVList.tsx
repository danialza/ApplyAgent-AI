"use client";

import { useState } from "react";
import { deleteCV } from "@/lib/api";
import type { CV } from "@/lib/types";

interface Props {
  cvs: CV[];
  onDelete: (cvId: number) => void;
  onError: (message: string) => void;
}

export default function UploadedCVList({ cvs, onDelete, onError }: Props) {
  const [deletingId, setDeletingId] = useState<number | null>(null);

  if (cvs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">
        No CVs uploaded yet.
      </div>
    );
  }

  async function handleDelete(cvId: number) {
    setDeletingId(cvId);
    try {
      await deleteCV(cvId);
      onDelete(cvId);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
      {cvs.map((cv) => (
        <li key={cv.id} className="flex items-center justify-between px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-slate-900">
              {cv.name || cv.filename}
            </p>
            <p className="truncate text-xs text-slate-500">
              {cv.filename}
              {cv.skills.length > 0 && (
                <>
                  {" · "}
                  {cv.skills.slice(0, 4).join(", ")}
                  {cv.skills.length > 4 && ` +${cv.skills.length - 4} more`}
                </>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={() => handleDelete(cv.id)}
            disabled={deletingId === cv.id}
            className="ml-4 rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            {deletingId === cv.id ? "Deleting…" : "Delete"}
          </button>
        </li>
      ))}
    </ul>
  );
}
