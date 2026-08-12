# Dorico Command Catalog (derived)

**Source:** derived from `keycommands.json` shipped with **Dorico 6** (Steinberg).
Only the command *identifiers* are listed here (not Steinberg's key-binding
assignments). The Remote Control API executes these same command IDs.

**340 unique commands.** Regenerate with `scripts/extract_catalog.ps1`.

> Legend for our integration status (fill in as we probe): \`V\` verified kOK ・ \`B\` broken/kUnknownCommand ・ blank = untested.

## Application  (1)

- `Application.Preferences`

## Edit  (16)

- `Edit.Copy`
- `Edit.CreateFrameBreak`
- `Edit.CreateMarker`
- `Edit.CreateSystemBreak`
- `Edit.CrossStaffAbove`
- `Edit.CrossStaffBelow`
- `Edit.Cut`
- `Edit.Delete`
- `Edit.FlipItem`
- `Edit.GoTo`
- `Edit.Paste`
- `Edit.Redo`
- `Edit.SelectAll`
- `Edit.SelectMore`
- `Edit.SelectNone`
- `Edit.Undo`

## Engrave  (2)

- `Engrave.EnterEdit`
- `Engrave.Options`

## EventEdit  (57)

- `EventEdit.DurationLengthen`
- `EventEdit.DurationLengthenByGrid`
- `EventEdit.DurationShorten`
- `EventEdit.DurationShortenByGrid`
- `EventEdit.EditExistingOrEnterStepTimeInput`
- `EventEdit.MoveLeft`
- `EventEdit.MoveLeftByGrid`
- `EventEdit.MoveRight`
- `EventEdit.MoveRightByGrid`
- `EventEdit.MoveToStaveAbove`
- `EventEdit.MoveToStaveBelow`
- `EventEdit.NavigateDown`
- `EventEdit.NavigateDownExtend`
- `EventEdit.NavigateDownExtendBottom`
- `EventEdit.NavigateDownNextStave`
- `EventEdit.NavigateLeft`
- `EventEdit.NavigateLeftBar`
- `EventEdit.NavigateLeftExtend`
- `EventEdit.NavigateLeftExtendBar`
- `EventEdit.NavigateNextFragment`
- `EventEdit.NavigateNextItemSamePosition`
- `EventEdit.NavigatePreviousFragment`
- `EventEdit.NavigatePreviousItemSamePosition`
- `EventEdit.NavigateRight`
- `EventEdit.NavigateRightBar`
- `EventEdit.NavigateRightExtend`
- `EventEdit.NavigateRightExtendBar`
- `EventEdit.NavigateUp`
- `EventEdit.NavigateUpExtend`
- `EventEdit.NavigateUpExtendTop`
- `EventEdit.NavigateUpNextStave`
- `EventEdit.Nudge?Direction=Down&Amount=kALittle`
- `EventEdit.Nudge?Direction=Down&Amount=kALittle&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Down&Amount=kALot`
- `EventEdit.Nudge?Direction=Down&Amount=kALot&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Down&Amount=kATinyAmount`
- `EventEdit.Nudge?Direction=Down&Amount=kModerate`
- `EventEdit.Nudge?Direction=Left&Amount=kALittle`
- `EventEdit.Nudge?Direction=Left&Amount=kALittle&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Left&Amount=kALot`
- `EventEdit.Nudge?Direction=Left&Amount=kALot&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Left&Amount=kATinyAmount`
- `EventEdit.Nudge?Direction=Left&Amount=kModerate`
- `EventEdit.Nudge?Direction=Right&Amount=kALittle`
- `EventEdit.Nudge?Direction=Right&Amount=kALittle&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Right&Amount=kALot`
- `EventEdit.Nudge?Direction=Right&Amount=kALot&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Right&Amount=kATinyAmount`
- `EventEdit.Nudge?Direction=Right&Amount=kModerate`
- `EventEdit.Nudge?Direction=Up&Amount=kALittle`
- `EventEdit.Nudge?Direction=Up&Amount=kALittle&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Up&Amount=kALot`
- `EventEdit.Nudge?Direction=Up&Amount=kALot&AlternateNudgeMode=true`
- `EventEdit.Nudge?Direction=Up&Amount=kATinyAmount`
- `EventEdit.Nudge?Direction=Up&Amount=kModerate`
- `EventEdit.ShowAllChordDiagramVariantsForPlayer`
- `EventEdit.ShowNextChordDiagramVariant`

## File  (9)

- `File.Close`
- `File.CloseProject`
- `File.Exit`
- `File.New`
- `File.Open`
- `File.Open?FilterID=MusicXMLImportFilter`
- `File.Save`
- `File.SaveAs`
- `File.ScoreInfo`

## Help  (1)

- `Help.ShowOverlay`

## JumpBar  (2)

- `JumpBar.CommandsMode`
- `JumpBar.GotoMode`

## NoteEdit  (8)

- `NoteEdit.PitchDown`
- `NoteEdit.PitchDownChromatic`
- `NoteEdit.PitchDownOctave`
- `NoteEdit.PitchUp`
- `NoteEdit.PitchUpChromatic`
- `NoteEdit.PitchUpOctave`
- `NoteEdit.RespellUsingNoteNameAbove`
- `NoteEdit.RespellUsingNoteNameBelow`

## NoteInput  (123)

- `NoteInput.CreateBarLine`
- `NoteInput.CreateClef`
- `NoteInput.CreateCue`
- `NoteInput.CreateDynamic`
- `NoteInput.CreateGraceNote`
- `NoteInput.CreateKeySignature`
- `NoteInput.CreateOrnament`
- `NoteInput.CreatePause`
- `NoteInput.CreatePlayingTechnique`
- `NoteInput.CreateRehearsalMark`
- `NoteInput.CreateSlashedVoice`
- `NoteInput.CreateSystemText?ParagraphStyleID=paragraph.defaulttext`
- `NoteInput.CreateTempo`
- `NoteInput.CreateText?ParagraphStyleID=paragraph.defaulttext`
- `NoteInput.CreateTimeSignature`
- `NoteInput.CreateVoice`
- `NoteInput.CycleNumDots`
- `NoteInput.DeleteLeft`
- `NoteInput.DeleteRight`
- `NoteInput.EditLockPosition`
- `NoteInput.EndTupletRun`
- `NoteInput.Enter`
- `NoteInput.Exit`
- `NoteInput.ExtendDown`
- `NoteInput.ExtendUp`
- `NoteInput.ForceDuration`
- `NoteInput.Fret?FretPosition=0`
- `NoteInput.Fret?FretPosition=1`
- `NoteInput.Fret?FretPosition=2`
- `NoteInput.Fret?FretPosition=3`
- `NoteInput.Fret?FretPosition=4`
- `NoteInput.Fret?FretPosition=5`
- `NoteInput.Fret?FretPosition=6`
- `NoteInput.Fret?FretPosition=7`
- `NoteInput.Fret?FretPosition=8`
- `NoteInput.Fret?FretPosition=9`
- `NoteInput.GraceNoteSlashToggle`
- `NoteInput.GridResolutionDecrease`
- `NoteInput.GridResolutionIncrease`
- `NoteInput.HairpinStart?Type=kGradualDynamicDown&GradualDynamicType=kCrescOrDim`
- `NoteInput.HairpinStart?Type=kGradualDynamicDown&GradualDynamicType=kMessaDiVoce`
- `NoteInput.HairpinStart?Type=kGradualDynamicUp&GradualDynamicType=kCrescOrDim`
- `NoteInput.HairpinStart?Type=kGradualDynamicUp&GradualDynamicType=kMessaDiVoce`
- `NoteInput.HairpinStop`
- `NoteInput.InsertScopeCycle`
- `NoteInput.Locked`
- `NoteInput.Mode`
- `NoteInput.MoveAdvance`
- `NoteInput.MoveDown`
- `NoteInput.MoveDownBottom`
- `NoteInput.MoveLeft`
- `NoteInput.MoveLeftBar`
- `NoteInput.MoveRight`
- `NoteInput.MoveRightBar`
- `NoteInput.MoveUp`
- `NoteInput.MoveUpTop`
- `NoteInput.NextVoice`
- `NoteInput.NoteValue?LogDuration=k128Note`
- `NoteInput.NoteValue?LogDuration=kBreve`
- `NoteInput.NoteValue?LogDuration=kCrotchet`
- `NoteInput.NoteValue?LogDuration=kDemiSemiQuaver`
- `NoteInput.NoteValue?LogDuration=kHemiDemiSemiQuaver`
- `NoteInput.NoteValue?LogDuration=kMinim`
- `NoteInput.NoteValue?LogDuration=kQuaver`
- `NoteInput.NoteValue?LogDuration=kSemibreve`
- `NoteInput.NoteValue?LogDuration=kSemiQuaver`
- `NoteInput.NoteValueLonger`
- `NoteInput.NoteValueShorter`
- `NoteInput.PedalLineStop`
- `NoteInput.Pitch?Pitch=A`
- `NoteInput.Pitch?Pitch=A&Down=true`
- `NoteInput.Pitch?Pitch=A&Up=true`
- `NoteInput.Pitch?Pitch=B`
- `NoteInput.Pitch?Pitch=B&Down=true`
- `NoteInput.Pitch?Pitch=B&Up=true`
- `NoteInput.Pitch?Pitch=C`
- `NoteInput.Pitch?Pitch=C&Down=true`
- `NoteInput.Pitch?Pitch=C&Up=true`
- `NoteInput.Pitch?Pitch=D`
- `NoteInput.Pitch?Pitch=D&Down=true`
- `NoteInput.Pitch?Pitch=D&Up=true`
- `NoteInput.Pitch?Pitch=E`
- `NoteInput.Pitch?Pitch=E&Down=true`
- `NoteInput.Pitch?Pitch=E&Up=true`
- `NoteInput.Pitch?Pitch=F`
- `NoteInput.Pitch?Pitch=F&Down=true`
- `NoteInput.Pitch?Pitch=F&Up=true`
- `NoteInput.Pitch?Pitch=G`
- `NoteInput.Pitch?Pitch=G&Down=true`
- `NoteInput.Pitch?Pitch=G&Up=true`
- `NoteInput.Pitch?Pitch=X`
- `NoteInput.PitchBeforeDurationToggle`
- `NoteInput.RepeatLast`
- `NoteInput.ReplyToComment`
- `NoteInput.RestMode`
- `NoteInput.Scissor`
- `NoteInput.ScissorWithMultipleSplits`
- `NoteInput.SetAccidental?Type=kFlat`
- `NoteInput.SetAccidental?Type=kNatural`
- `NoteInput.SetAccidental?Type=kSharp`
- `NoteInput.SetArticulation?Value=kAccent`
- `NoteInput.SetArticulation?Value=kMarcato`
- `NoteInput.SetArticulation?Value=kStaccatissimo`
- `NoteInput.SetArticulation?Value=kStaccato`
- `NoteInput.SetArticulation?Value=kStaccatoTenuto`
- `NoteInput.SetArticulation?Value=kStress`
- `NoteInput.SetArticulation?Value=kTenuto`
- `NoteInput.SetArticulation?Value=kUnstress`
- `NoteInput.SetDotted`
- `NoteInput.ShowCommentDialog`
- `NoteInput.ShowFingeringPopover`
- `NoteInput.ShowMIDITriggerRegionPopover`
- `NoteInput.ShowNoteInputOptions`
- `NoteInput.ShowPopoverForTransposingOrAddingNotes`
- `NoteInput.ShowRepeatsPopover`
- `NoteInput.SlurStart`
- `NoteInput.SlurStop`
- `NoteInput.StartChordSymbolInput`
- `NoteInput.StartEndChord`
- `NoteInput.StartFiguredBassInput`
- `NoteInput.StartLyricInput`
- `NoteInput.StartTupletRun`
- `NoteInput.Tie`

## OptionsDialog  (5)

- `OptionsDialog.CloseFind`
- `OptionsDialog.Filter`
- `OptionsDialog.Find`
- `OptionsDialog.NextResult`
- `OptionsDialog.PreviousResult`

## Page  (2)

- `Page.MoveBarsToNextSystem`
- `Page.MoveBarsToPreviousSystem`

## Play  (30)

- `Play.Forward`
- `Play.LaneSizeDecrease`
- `Play.LaneSizeIncrease`
- `Play.NavigateBackwards`
- `Play.NavigateForwards`
- `Play.NextFrame`
- `Play.PreviousFrame`
- `Play.Record`
- `Play.Refresh`
- `Play.RetrospectiveRecord`
- `Play.Rewind`
- `Play.SetKeyEditorTool1`
- `Play.SetKeyEditorTool2`
- `Play.SetKeyEditorTool3`
- `Play.SetKeyEditorTool4`
- `Play.SetKeyEditorTool5`
- `Play.SetKeyEditorTool6`
- `Play.SetPlayheadTo?PlayFromLocation=kSelection`
- `Play.SetPlayheadToFlowStart`
- `Play.ShowPlaybackOptions`
- `Play.SoloFromSelection`
- `Play.StartOrStop?PlayFromLocation=kLastStartPosition`
- `Play.StartOrStop?PlayFromLocation=kPlayhead`
- `Play.StartOrStop?PlayFromLocation=kSelection`
- `Play.StartOrStop?PlayFromLocation=kStartOfFlow`
- `Play.StartScrubPlayback`
- `Play.StartScrubSoloPlayback`
- `Play.Stop`
- `Play.UnMuteAll`
- `Play.UnSoloAll`

## Print  (5)

- `Print.LayoutsPanelSelectAll`
- `Print.PrintPreviewEnd`
- `Print.PrintPreviewHome`
- `Print.PrintPreviewPageDown`
- `Print.PrintPreviewPageUp`

## Project  (8)

- `Project.Ensemble.Choose`
- `Project.Flow.New`
- `Project.Flow.NotationOptions`
- `Project.Instrument.New`
- `Project.Layout.New?Type=kCustomScoreLayout`
- `Project.Layout.Options`
- `Project.SectionPlayer.New`
- `Project.SoloPlayer.New`

## Script  (1)

- `Script.RunLastScript`

## ScrubPlayback  (2)

- `ScrubPlayback.StepBackwards`
- `ScrubPlayback.StepForwards`

## Setup  (10)

- `Setup.Delete`
- `Setup.Insert`
- `Setup.MoveDown`
- `Setup.MoveLeft`
- `Setup.MoveRight`
- `Setup.MoveToBottom`
- `Setup.MoveToEnd`
- `Setup.MoveToStart`
- `Setup.MoveToTop`
- `Setup.MoveUp`

## TextEditor  (6)

- `TextEditor.ConvertUnicode`
- `TextEditor.DecreaseFontSize`
- `TextEditor.IncreaseFontSize`
- `TextEditor.ToggleBold`
- `TextEditor.ToggleItalic`
- `TextEditor.ToggleUnderline`

## UI  (7)

- `UI.Escape`
- `UI.SetFocus?Value=kBottomPane`
- `UI.SetFocus?Value=kLeftPane`
- `UI.SetFocus?Value=kRightPane`
- `UI.SetFocus?Value=kScoreView`
- `UI.ShowJumpBar`
- `UI.ToggleDefaultMouseClickTool`

## Video  (1)

- `Video.ShowWindow`

## View  (16)

- `View.FullScreen`
- `View.HideInvisibles`
- `View.MoveViewportDown`
- `View.MoveViewportLeft`
- `View.MoveViewportNextPage`
- `View.MoveViewportPreviousPage`
- `View.MoveViewportRight`
- `View.MoveViewportToEnd`
- `View.MoveViewportToStart`
- `View.MoveViewportUp`
- `View.OptionsShowDialog`
- `View.ShowSystemTrack`
- `View.ZoomDialog`
- `View.ZoomIn`
- `View.ZoomOut`
- `View.ZoomWholePage`

## Window  (28)

- `Window.CloseTab`
- `Window.HideAllPanels`
- `Window.KeyEditorZoomInHorizontal`
- `Window.KeyEditorZoomInVertical`
- `Window.KeyEditorZoomOutHorizontal`
- `Window.KeyEditorZoomOutVertical`
- `Window.Minimise`
- `Window.Mixer`
- `Window.NewTab`
- `Window.NewWindow`
- `Window.NextLayout`
- `Window.NextTab`
- `Window.PreviousLayout`
- `Window.PreviousTab`
- `Window.ShowBottomPanel`
- `Window.ShowLeftPanel`
- `Window.ShowRightPanel`
- `Window.ShowToolbar`
- `Window.SwitchLayoutAspectType?LayoutAspectType=kContinuousVerticalViewAspect`
- `Window.SwitchLayoutAspectType?LayoutAspectType=kGalleyViewAspect`
- `Window.SwitchLayoutAspectType?LayoutAspectType=kPageViewAspect`
- `Window.SwitchMode?WindowMode=kEngraveMode`
- `Window.SwitchMode?WindowMode=kPlayMode`
- `Window.SwitchMode?WindowMode=kPrintMode`
- `Window.SwitchMode?WindowMode=kSetupMode`
- `Window.SwitchMode?WindowMode=kWriteMode`
- `Window.ToggleScoreAndPartLayout`
- `Window.Transport`


