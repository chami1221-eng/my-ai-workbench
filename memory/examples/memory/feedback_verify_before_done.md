---
name: feedback-verify-before-done
description: Never report a file, automation or fix as finished without checking the real artifact.
metadata:
  type: feedback
---

# Verify before saying done

Rule: before saying "created", "deployed" or "fixed", open the real artifact (list the folder,
run the script, query the record) and quote what you saw.

**Why / risk:** a handoff can say a workshop deck was "created and placed in the shared folder"
when it does not exist, and later sessions repeat the claim without checking.
Example path named in this note: `~/shared/acme-city/20260914_workshop_agenda.pptx` (filecheck.py reports it as GHOST).

**How to apply:**
- "It exists" is not "it works". Check both when it matters.
- If the check is impossible right now, say "not verified" explicitly instead of "done".

<!-- superseded:260905 by:check the artifact, not the log -->
Checking the tool's own success message is enough.
