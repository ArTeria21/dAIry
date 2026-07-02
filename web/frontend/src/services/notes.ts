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
