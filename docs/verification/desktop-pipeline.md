# Desktop pipeline editor

**Add operation** opens a scrollable picker with one card per registered
operation version and model choice, sorted alphabetically by display name.
The search field takes focus when the picker opens. Type any part of a name
to filter the cards regardless of case. Enter adds the first match, and
clearing the field restores the full list.
Cards show required, optional and setting-dependent inputs, outputs, and
either conventional processing or the AI runtime, supported devices and model.
Click a card or focus it and press Enter to append the operation. Escape or
Cancel dismisses the picker.
Each stage card shows Move up, Move down and Remove to the left of the fold
button. Moving a stage preserves its expanded or folded state. Remove asks for
confirmation with the stage's current position and name. Cancel is the default.
Added instances start with unique
IDs and descriptor defaults and pass through the shared asset and context
checks. The cards retain their toggles, parameters, asset details and recovery
actions. The batch setup summary reflects editor changes.

**New pipeline** asks for confirmation, with Cancel selected by default.
Confirming starts an empty untitled pipeline and clears the preset selection.
**Open JSON** validates a portable pipeline before replacing the editor state.
**Save JSON** writes the editor's current order, enabled flags and parameters.
Successful edits and opens update the separate last-used pipeline. The editor
restores it on startup. Malformed imports and failed writes display an error and
retain the preceding valid state. Explicitly opened files receive changes only
through a subsequent save to that path.

The preset picker lists JSON filenames in the configured folder. The headless
preset test covers discovery, custom paths, restart with saved and edited
presets, and recovery when the preset file disappears. The Qt editor test
checks picker refresh, saving a preset, canceling and confirming a switch,
and keeping the selection after a failed import. The desktop settings test
checks that a folder change updates the picker immediately.

## Focused verification

On 2026-11-25, three offscreen Qt checks exercised the operation picker.
`test_operation_picker_filters_by_name_as_you_type` checked initial focus,
live substring matching, an empty result, clearing the field and keyboard
selection of a filtered model.
`test_pipeline_editor_actions_json_dialogs_and_restart` checked the installed
catalog's display order and metadata, dismissed the picker with Escape, and
added separate instances using the mouse and keyboard.
`test_operation_picker_keeps_version_and_model_choices_distinct` checked
case-insensitive sorting, numeric version order, model selection and label
fit at 360 pixels wide. Offscreen renders were inspected at 560 and 360 pixels.

On 2026-11-19, the offscreen Qt test
`test_pipeline_editor_actions_json_dialogs_and_restart` checked the popup
entries against every registered operation, version and model choice. It
dismissed the popup with Escape, added two instances of the same operation,
and checked that canceling removal preserved the pipeline and its autosave.
Confirming removal deleted only the selected instance. The test also checked
button order and visibility on a folded card, moved it while folded, and
exercised both confirmation choices for **New pipeline**. The headless test
`test_new_pipeline_replaces_working_state_only_after_autosave` restored the
empty pipeline on restart, verified the saved preset was intact and checked
that a failed autosave retained the previous working pipeline and selection.

On 2026-11-18, verification used Python 3.14.7, PySide6 Essentials 6.12.0 and the
prepared DirectML environment on Windows 11.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pipeline.py -v -k add_registered -k nonempty_startup -k invalid_import -k json_roundtrip -k headless_registry
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -v -k pipeline_change_during_freeze
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k static_qt_import_boundary -k headless_import_boundary
```

The focused checks cover duplicate instances, boundary controls, parameter and
toggle retention across moves, and dependency revalidation with a synthetic
model fixture. They also cover JSON on Unicode paths, canceling Open, rejecting
a malformed import, simulated atomic-save denial with its diagnostic traceback,
and startup restoration. The batch integration check verifies that a changed
definition at freeze time leaves the saved job queued for review. Fresh-process
checks verify the Qt import boundary.

Qt checks used offscreen rendering and simulated the filenames returned by the
OS file dialogs. Portrait-model acceptance and CUDA hardware coverage remain pending.

## Stage cards and blending

On 2026-11-19, focused checks in the prepared environment covered the card
header, required and optional tags, model menu, editable parameters, and both
preview actions. Offscreen renders of the Alibaba card were inspected at 450
pixels wide with the installed local checkpoints and with an empty model folder.
The recovery test used a synthetic checkpoint and verified that the recovered
stage stays off until enabled.

The processing checks covered exact input and full-effect endpoints, a partial
linear-RGB blend, immutable pixel storage, JSON round trips, legacy checkpoint
identity, and matching sequential and staged JPEG exports. The Qt preview test
changed blending against an already edited input, checked the displayed result,
and saved the draft into the pipeline.

On 2026-11-25, focused checks covered Normal, Darken, Multiply, Lighten,
Screen, Overlay and Difference on RGB samples at zero, quarter and full opacity.
A synthetic color ramp with a circular edit was visually inspected across all
seven modes. Pixels outside the circle matched the input exactly. An offscreen
Qt test changed a brightening exposure to Darken in the stage preview, checked
that the displayed pixels returned to their input value, and saved the mode
to the card and pipeline. Sequential and staged jobs using Screen at 40%
opacity produced matching JPEG pixels from their frozen settings.
