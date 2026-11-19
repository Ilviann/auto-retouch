"""Load the shipped presets through the editor and check their stage dependencies."""

from dataclasses import replace
import hashlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.operations.alibaba_skin import SMOOTHING, SMOOTHING_MODEL
from retouching_tool.operations.exposed_skin import MODEL as MASK_MODEL
from retouching_tool.operations.skin_tone_evening import DESCRIPTOR as TONE
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.image_state import EXPOSED_SKIN


PRESETS = Path(__file__).resolve().parents[1] / "pipelines"


class PortraitPresetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(log.close)
        self.editor = PipelineService(self.root / "models", "directml", log, pytorch_backend="cpu",
                                      autosave=self.root / "state.json", preset_folder=PRESETS)

    def supply_fixtures(self):
        content = b"Preset validation fixture"
        digest = hashlib.sha256(content).hexdigest()
        manifests = []
        for manifest in (MASK_MODEL, SMOOTHING_MODEL):
            model_file, = manifest.files
            path = self.editor.checkpoints / model_file.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            manifests.append(replace(manifest, files=(replace(model_file, sha256=digest),)))
        self.editor.catalog = ModelCatalog(manifests)

    def test_shipped_presets_discover_load_and_target_current_skin(self):
        self.supply_fixtures()
        discovered = {p.stem: p for p in self.editor.list_presets()}
        strengths = {}
        for name in ("Natural", "Beauty"):
            with self.subTest(name=name):
                status = self.editor.load(discovered[name])
                self.assertTrue(status.executable)
                self.assertEqual(status.definition.name, name)
                self.assertTrue(all(item.state == "ready" for item in status.stages))
                stages = self.editor.preflight().stages
                self.assertEqual([s.operation for s in stages[:2]], ["core.white_balance", "core.auto_exposure"])
                consumers = [s for s in stages if s.operation in (TONE.operation, SMOOTHING.operation)]
                strengths[name] = next(s.parameter("strength") for s in consumers if s.operation == TONE.operation)
                smoothing = [s for s in consumers if s.operation == SMOOTHING.operation]
                self.assertEqual(len(smoothing), int(name == "Beauty"))
                for stage in consumers:
                    descriptor = self.editor.registry.descriptor(stage.operation, stage.operation_version)
                    self.assertIn(EXPOSED_SKIN, descriptor.active_required_inputs(stage))
                self.assertFalse(any(s.operation == "retouch.alibaba_blemish_cleanup" for s in stages))
        self.assertLess(strengths["Natural"], strengths["Beauty"])

    def test_missing_mask_blocks_edits_and_recovery_requires_enabling_analysis(self):
        status = self.editor.load(PRESETS / "Beauty.json")
        masks = [item for item in status.stages if item.stage.model_ref == MASK_MODEL.ref]
        self.assertEqual(len(masks), 2)
        self.assertTrue(all(not item.stage.enabled and item.issues[0].code == "MODEL_MISSING" for item in masks))
        self.assertFalse(status.executable)
        self.supply_fixtures()
        self.editor.recheck()
        for item in masks:
            self.assertFalse(next(s.stage.enabled for s in self.editor.status.stages if s.stage.id == item.stage.id))
            self.editor.set_enabled(item.stage.id, True)
        smoothing, = [s.stage for s in self.editor.status.stages if s.stage.operation == SMOOTHING.operation]
        self.assertFalse(smoothing.enabled)
        self.editor.set_enabled(smoothing.id, True)
        self.assertTrue(self.editor.status.executable)

    def test_working_edits_restore_and_save_as_a_separate_preset(self):
        self.supply_fixtures()
        path = PRESETS / "Natural.json"
        original = self.editor.load(path).definition
        tone = next(s for s in original.stages if s.operation == TONE.operation)
        self.editor.set_parameter(tone.id, "strength", .42)
        self.assertTrue(self.editor.has_unsaved_changes)
        self.editor.restore()
        self.assertEqual(self.editor.preset_path, path)
        self.assertTrue(self.editor.has_unsaved_changes)
        custom = self.root / "Custom.json"
        self.editor.save(custom)
        self.assertEqual(next(s for s in self.editor.load(custom).definition.stages
                              if s.id == tone.id).parameter("strength"), .42)
        self.assertEqual(self.editor.load(path).definition, original)
