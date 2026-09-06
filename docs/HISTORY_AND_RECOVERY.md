# History & Recovery: Field-Tested Methodology

Lessons from a real recovery session (CST 2024, a battery-pack assembly with
~180 imported components, split-shell solids, and a metallized EMC layer).
Every claim below was verified against the running application, most of them
the hard way.

## 1. The modeler history is the model

- The geometry you see is a **cache** of the history replay, not the source
  of truth. The recipe lives in `Model/3D/ModelHistory.json` (plus a
  `ModelHistory.json_as` autosave twin that CST deletes on clean opens).
- **History entries are atomic.** If an entry aborts mid-way (runtime error
  box, process death), CST rolls back *all* of that entry's effects — but
  keeps the effects of every earlier entry. A crashed boolean merge can
  therefore silently resurrect geometry you had already removed, and a
  partial entry can leave some of its own operations applied. After any
  crash: re-verify solid counts, transforms, and component lists against
  expectations (see §5 for the audit tools).
- Entries that fail to compile or error out are **not persisted** to the
  saved history; entries whose effects "stuck" are. Audit the saved file
  (`grep` the captions and `Solid.*` / `Component.Delete` lines) to know
  what a replay will actually do.

## 2. Restoring deleted geometry: `full_history_rebuild()`

`Model3D.full_history_rebuild(timeout)` (documented in
`Online Help/Python/source/cst.interface.html`) replays the entire history
in place — the programmatic equivalent of the GUI History List rebuild.

**This is the recovery path of first resort when a solid was deleted by a
later history entry** (or when the history file was corrected externally):
remove the offending entries, then replay. In one real case a housing's
middle section that had been deleted was fully restored — with correct
geometry — by (1) closing the project, (2) removing the two offending
entries from `ModelHistory.json` (JSON-parse it, filter entries by
`caption`, rewrite; keep a backup), and (3) calling
`full_history_rebuild()`.

What does **not** work:

- Same-process project `close()` + `open_project()`: CST re-attaches the
  in-memory geometry and does **not** replay. Verified twice.
- Launching a fresh DE *while caches exist*: CST loads the stale geometry
  cache. To force a replay from disk you must hide
  `Model/3D/Model.sab`, `Model.alc`, `Model.bwc` (rename, keep backups)
  **and** use a genuinely fresh process. Prefer the in-place rebuild.
- Sandbox/automation note: launching CST from a sandboxed shell attaches it
  to the sandbox job object (automation endpoint never registers; process
  dies with the job). Creating the process via WMI
  (`Win32_Process.Create`) detaches it properly — but WMI process creation
  is commonly denied by sandbox policies.

Pitfalls around the rebuild:

- `IsBuildingModel()` can keep returning `True` **after the rebuild has
  finished** (stale flag). Don't wait on it forever; probe reality instead
  (solid count via §5).
- After a rebuild, `Model3D.Save()` (the python method) saves reliably even
  with the stale build flag; `project.save()` may stall.
- A full replay re-runs every persisted entry — including all component
  deletions and material assignments. That is usually what you want, but
  re-check the model afterwards anyway.

## 3. Getting values out of VBA: the marker-file bridge

`add_to_history` macros **cannot return values** to the caller. To read
anything (solid counts, bounding boxes, error codes), generate a wrapper
macro that writes to a file, then read the file:

```vb
On Error Resume Next
Dim f As Integer
f = FreeFile
Open "C:/path/out.txt" For Output As #f
Print #f, "ok"
Print #f, CStr(Solid.GetNumberOfShapes)
Close #f
On Error GoTo 0
```

Poll for the file after the COM call returns (small latency is normal).
This repo implements the bridge as `CSTClient.execute_vba_query()` /
`cst_execute_vba_query` with expression validation.

## 4. Dialog traps

- CST raises **modal error boxes** on VBA runtime errors ("History Error",
  bare `VBA`-titled `#32770` boxes, Qt `RemoteUI::GenericDialog` hosts
  titled `CST MICROWAVE STUDIO 2024`). Any of these blocks the pending
  `add_to_history` COM call **indefinitely** — observed 540 s stalls, and a
  modeler process death while blocked.
- Always run a **dialog watcher** (this repo: `DialogWatcher`) around every
  history macro, and pass a `timeout` to `add_to_history()` (the official
  API supports it). After a timeout, sweep dialogs once more: a lingering
  error host can outlive the COM exception.
- Save prompts are Qt windows whose buttons are **"否"/"Don't Save"** —
  Win32 button scans that only look for "OK"/"Yes" find nothing. Click them
  deliberately (UIAutomation) or avoid triggering them (`Project.close()`
  closes without saving, by design).
- `DesignEnvironment.set_quiet_mode(True)` suppresses message boxes, except
  those requiring user input.

## 5. Auditing the model (and the traps that bit us)

- **`get_tree_items()` is the only trustworthy enumeration.**
  `SelectTreeItem` returns 0 even for names that do not exist — it is
  useless as an existence oracle. Component audits (e.g. finding the ~100
  empty leftover components after a CAD import) must use
  `get_tree_items()`, or `Component.Delete` return codes (0 = deleted,
  `-2147418113` = refused or absent) as ground truth.
- **Loose bounding boxes are control-point hulls.**
  `GetLooseBoundingBoxOfShape` on a B-spline solid returns a box that can
  massively overestimate the real extent. Two real failures came from
  treating bbox containment as geometric containment: a "redundant" solid
  that was actually a load-bearing middle section, and a bogus gap
  estimate. Never conclude "A contains B" from bounding boxes; use a real
  boolean probe or a solid count delta.
- **ACIS boolean seams are picky.** Touching solids that boolean-unite
  cleanly on one interface can be rejected on another ("Error in boolean
  operation", "Two or more vertices of one body mapped to a single entity
  of other body"). Test each pair; `Solid.HealShape` on the bodies changes
  outcomes in both directions. Failed pairs are not a reason to give up on
  the ones that work.

## 6. Recommended recovery runbook

1. **Triage**: `cst_solid_count` + `cst_list_tree_items(prefix="Components")`
   — compare against the expected state.
2. **History audit**: read `Model/3D/ModelHistory.json`, list captions and
   geometry-affecting lines (`Solid.Add/Delete/HealShape/Imprint`,
   `Component.Delete`). Failed entries do not persist; suspicious ones do.
3. **Repair**: close the project, back up, remove the offending entries
   from the JSON (filter by `caption`), reopen.
4. **Replay**: `cst_full_history_rebuild` (watcher on, generous timeout).
5. **Verify**: solid count + tree listing; do not trust
   `IsBuildingModel()`.
6. **Persist**: `Model3D.Save()`; verify `ModelHistory.json` mtime changed.
