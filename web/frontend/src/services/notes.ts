export class NoteEditError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export type NoteEditResult = {
  id: string;
  new_sha256: string;
};

export type NoteDeleteResult = {
  id: string;
  deleted: boolean;
};

export async function saveNoteText(
  noteId: string,
  newText: string,
  expectedSha256: string,
): Promise<NoteEditResult> {
  const response = await fetch(`/api/notes/${encodeURIComponent(noteId)}`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_text: newText, expected_sha256: expectedSha256 }),
  });
  if (!response.ok) {
    throw new NoteEditError("NOTE EDIT FAILED", response.status);
  }
  return response.json() as Promise<NoteEditResult>;
}

export async function deleteNote(noteId: string, expectedSha256: string): Promise<NoteDeleteResult> {
  const response = await fetch(`/api/notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_sha256: expectedSha256 }),
  });
  if (!response.ok) {
    throw new NoteEditError("NOTE DELETE FAILED", response.status);
  }
  return response.json() as Promise<NoteDeleteResult>;
}
