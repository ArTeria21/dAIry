import { useEffect, useState } from "react";

import { chromeTextClass, readingTextClass } from "../design/theme";
import { NoteEditError, saveNoteText } from "../services/notes";
import { cx } from "../ui/classNames";

type NoteEditorProps = {
  noteId: string;
  rawText: string;
  rawTextSha256: string;
  onReload: () => Promise<void>;
};

export function NoteEditor({ noteId, onReload, rawText, rawTextSha256 }: NoteEditorProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(rawText);
  const [unsavedVersion, setUnsavedVersion] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [disabled, setDisabled] = useState(false);

  useEffect(() => {
    if (!editing || status === "NOTE CHANGED ELSEWHERE — RELOADED") {
      setDraft(rawText);
    }
  }, [editing, rawText, status]);

  if (!editing) {
    return (
      <div className="grid gap-2">
        {status ? <p className={cx(chromeTextClass, "text-[10px] text-slate")}>{status}</p> : null}
        <button
          className={cx(
            chromeTextClass,
            "w-fit rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate disabled:opacity-50",
          )}
          disabled={disabled}
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
      setStatus("SAVED · ENRICHMENT WILL UPDATE LATER");
      await onReload();
    } catch (error) {
      if (error instanceof NoteEditError && error.status === 409) {
        const attempted = draft;
        await onReload();
        setUnsavedVersion(attempted);
        setStatus("NOTE CHANGED ELSEWHERE — RELOADED");
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
