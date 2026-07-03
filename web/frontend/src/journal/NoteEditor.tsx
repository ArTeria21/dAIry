import { useEffect, useRef, useState } from "react";

import { chromeTextClass, readingTextClass } from "../design/theme";
import { NoteEditError, saveNoteText } from "../services/notes";
import { cx } from "../ui/classNames";

type NoteEditorProps = {
  noteId: string;
  rawText: string;
  rawTextSha256: string;
  onReload: () => Promise<ReloadedNoteText | null>;
};

type ReloadedNoteText = {
  rawText: string;
  rawTextSha256: string;
};

const savedStatus = "SAVED · ENRICHMENT WILL UPDATE LATER";
const reloadedStatus = "NOTE CHANGED ELSEWHERE — RELOADED";

export function NoteEditor({ noteId, onReload, rawText, rawTextSha256 }: NoteEditorProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(rawText);
  const [unsavedVersion, setUnsavedVersion] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
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
        <button
          className={cx(
            chromeTextClass,
            "w-fit rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate disabled:opacity-50",
          )}
          disabled={disabled || saving}
          onClick={() => {
            setEditing(true);
            setDraft(rawText);
            setUnsavedVersion("");
            setStatus("");
          }}
          type="button"
        >
          EDIT
        </button>
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
        <button
          className={cx(
            chromeTextClass,
            "rounded-[2px] bg-ink-black px-3 py-2 text-[10px] text-cream-paper disabled:opacity-50",
          )}
          disabled={saving}
          onClick={save}
          type="button"
        >
          SAVE
        </button>
        <button
          className={cx(chromeTextClass, "rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate")}
          disabled={saving}
          onClick={() => {
            setEditing(false);
            setDraft(rawText);
            setUnsavedVersion("");
            setStatus("");
          }}
          type="button"
        >
          CANCEL
        </button>
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
