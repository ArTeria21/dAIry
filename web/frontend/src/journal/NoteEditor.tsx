import { useEffect, useRef, useState } from "react";

import { chromeTextClass, readingTextClass } from "../design/theme";
import { deleteNote, NoteEditError, saveNoteText } from "../services/notes";
import { cx } from "../ui/classNames";
import { Button } from "../ui/primitives";

type NoteEditorProps = {
  noteId: string;
  rawText: string;
  rawTextSha256: string;
  onReload: () => Promise<ReloadedNoteText | null>;
  onDeleted?: (status: string) => Promise<void> | void;
};

type ReloadedNoteText = {
  rawText: string;
  rawTextSha256: string;
};

const savedStatus = "SAVED · ENRICHMENT WILL UPDATE LATER";
const reloadedStatus = "NOTE CHANGED ELSEWHERE — RELOADED";
const deletedStatus = "NOTE DELETED · MAP WILL UPDATE AFTER RE-ENRICHMENT";

export function NoteEditor({ noteId, onDeleted, onReload, rawText, rawTextSha256 }: NoteEditorProps) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState(rawText);
  const [unsavedVersion, setUnsavedVersion] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [pendingReloadSync, setPendingReloadSync] = useState(false);
  const [savedReloadBaselineSha, setSavedReloadBaselineSha] = useState("");
  const pendingReloadTextRef = useRef<ReloadedNoteText | null>(null);

  useEffect(() => {
    if (pendingReloadSync) {
      setDraft(pendingReloadTextRef.current?.rawText ?? rawText);
      pendingReloadTextRef.current = null;
      setPendingReloadSync(false);
      return;
    }

    if (!editing) {
      setDraft(rawText);
      if (status === savedStatus && savedReloadBaselineSha && rawTextSha256 !== savedReloadBaselineSha) {
        setSavedReloadBaselineSha("");
        setStatus("");
      }
    }
  }, [editing, pendingReloadSync, rawText, rawTextSha256, savedReloadBaselineSha, status]);

  if (!editing) {
    return (
      <div className="grid gap-2">
        {status ? <p className={cx(chromeTextClass, "text-[10px] text-slate")}>{status}</p> : null}
        {confirmingDelete ? (
          <div className="grid gap-2">
            <p className={cx(chromeTextClass, "text-[10px] text-slate")}>DELETE THIS NOTE?</p>
            <div className="flex flex-wrap gap-2">
              <Button disabled={deleting} onClick={confirmDelete} variant="dark">
                CONFIRM DELETE
              </Button>
              <Button disabled={deleting} onClick={() => setConfirmingDelete(false)} variant="ghost">
                CANCEL
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={disabled || saving || deleting}
              onClick={() => {
                setEditing(true);
                setDraft(rawText);
                setUnsavedVersion("");
                setStatus("");
              }}
              variant="ghost"
            >
              EDIT
            </Button>
            <Button
              disabled={disabled || saving || deleting}
              onClick={() => {
                setConfirmingDelete(true);
                setStatus("");
              }}
              variant="ghost"
            >
              DELETE
            </Button>
          </div>
        )}
      </div>
    );
  }

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      await saveNoteText(noteId, draft, rawTextSha256);
      setEditing(false);
      setUnsavedVersion("");
      setSavedReloadBaselineSha(rawTextSha256);
      setStatus(savedStatus);
      try {
        await onReload();
      } catch {
        setSavedReloadBaselineSha("");
        setStatus("RELOAD FAILED");
      }
    } catch (error) {
      if (error instanceof NoteEditError && error.status === 409) {
        const attempted = draft;
        try {
          pendingReloadTextRef.current = await onReload();
          setPendingReloadSync(true);
          setUnsavedVersion(attempted);
          setStatus(reloadedStatus);
        } catch {
          setStatus("RELOAD FAILED");
        }
      } else if (error instanceof NoteEditError && error.status === 502) {
        setEditing(false);
        setDisabled(true);
        setStatus("EDITING DISABLED");
      } else {
        setStatus("SAVE FAILED");
      }
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    setStatus("");
    try {
      await deleteNote(noteId, rawTextSha256);
      setConfirmingDelete(false);
      setDisabled(true);
      setStatus(deletedStatus);
      await onDeleted?.(deletedStatus);
    } catch (error) {
      setConfirmingDelete(false);
      if (error instanceof NoteEditError && error.status === 409) {
        try {
          pendingReloadTextRef.current = await onReload();
          setPendingReloadSync(true);
          setStatus(reloadedStatus);
        } catch {
          setStatus("RELOAD FAILED");
        }
      } else if (error instanceof NoteEditError && error.status === 404) {
        setDisabled(true);
        setStatus("NOTE ALREADY DELETED");
        await onDeleted?.("NOTE ALREADY DELETED");
      } else if (error instanceof NoteEditError && error.status === 502) {
        setDisabled(true);
        setStatus("EDITING DISABLED");
      } else {
        setStatus("DELETE FAILED");
      }
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="grid gap-3">
      {status ? <p className={cx(chromeTextClass, "text-[10px] text-slate")}>{status}</p> : null}
      <textarea
        className={cx(
          readingTextClass,
          "min-h-[180px] rounded-[2px] border border-hairline bg-cream-paper p-3 text-[15px] leading-7 outline-none",
        )}
        disabled={saving}
        onChange={(event) => setDraft(event.target.value)}
        value={draft}
      />
      <div className="flex flex-wrap gap-2">
        <Button disabled={saving} onClick={save} variant="dark">
          SAVE
        </Button>
        <Button
          disabled={saving}
          onClick={() => {
            setEditing(false);
            setDraft(rawText);
            setUnsavedVersion("");
            setStatus("");
          }}
          variant="ghost"
        >
          CANCEL
        </Button>
      </div>
      {unsavedVersion ? (
        <div className="grid gap-2 rounded-[2px] border border-hairline p-3">
          <span className={cx(chromeTextClass, "text-[10px] text-slate")}>YOUR UNSAVED VERSION</span>
          <p className={cx(readingTextClass, "whitespace-pre-wrap text-sm leading-6")}>{unsavedVersion}</p>
        </div>
      ) : null}
    </div>
  );
}
