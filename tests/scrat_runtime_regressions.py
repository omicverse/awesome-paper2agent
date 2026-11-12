import concurrent.futures
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import anndata as ad
import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "scrat-phenotype" / "src"))

import scrat_core as core


class ScratRuntimeRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "model.pt"
        torch.manual_seed(31)
        model = core.ScRATModel(input_dim=4, model_dim=8, heads=2, dropout=0.0)
        torch.save(
            {
                "format_version": 1,
                "state_dict": model.state_dict(),
                "model_config": {"input_dim": 4, "model_dim": 8, "heads": 2, "dropout": 0.0},
                "sample_key": "patient_id",
                "cell_type_key": "cell_type",
                "embedding_key": "X_pca",
                "feature_names": [f"X_pca:{index}" for index in range(4)],
                "label_classes": ["negative", "positive"],
                "crop_cells": 8,
                "pca_mean": None,
                "pca_components": None,
            },
            self.model_path,
        )

    def input_file(self, name, cells=8, samples=1, identical=False, multiple_types=False):
        count = cells * samples
        matrix = np.random.default_rng(101).normal(size=(count, 4)).astype(np.float32)
        if identical:
            matrix[:] = 1.0
        observation = pd.DataFrame(
            {
                "patient_id": [f"patient-{index // cells:02}" for index in range(count)],
                "Outcome": ["positive" if (index // cells) % 2 else "negative" for index in range(count)],
                "cell_type": ["B" if multiple_types and index % cells >= cells // 4 else "A" for index in range(count)],
            },
            index=[f"cell-{index}" for index in range(count)],
        )
        data = ad.AnnData(matrix, obs=observation)
        data.obsm["X_pca"] = matrix.copy()
        source = self.root / name
        data.write_h5ad(source)
        return source

    def prediction(self, source, stem="prediction", interpret=True, n_crops=1):
        paths = [self.root / f"{stem}.csv"]
        options = {}
        if interpret:
            paths.extend([self.root / f"{stem}-cells.csv", self.root / f"{stem}-types.csv"])
            options = {"cell_scores_path": str(paths[1]), "cell_type_scores_path": str(paths[2])}
        result = core.predict_h5ad(
            str(source), str(self.model_path), str(paths[0]), n_crops=n_crops,
            crop_cells=8, top_k=1, batch_size=4, device="cpu", seed=67, **options,
        )
        return result, paths

    def train(self, source, model_path, predictions_path):
        return core.train_h5ad(
            str(source), str(model_path), str(predictions_path), min_cells=8,
            n_pseudo=4, mixup_cells=8, crop_cells=8, train_crops=1, test_crops=1,
            model_dim=8, heads=2, dropout=0.0, epochs=1, batch_size=4,
            device="cpu", seed=37,
        )

    def test_identical_cells_keep_all_attention_ties(self):
        source = self.input_file("identical.h5ad", identical=True)
        _, paths = self.prediction(source)
        cells = pd.read_csv(paths[1])
        self.assertEqual(cells["hov"].tolist(), [16] * 8)
        self.assertEqual(cells["n_appearances"].tolist(), [1] * 8)

    def test_single_type_r_score_is_independent_of_sampling_fraction(self):
        for count in (8, 16):
            with self.subTest(cells=count):
                source = self.input_file(f"single-{count}.h5ad", cells=count)
                _, paths = self.prediction(source, stem=f"single-{count}", n_crops=3)
                cells = pd.read_csv(paths[1])
                types = pd.read_csv(paths[2])
                self.assertEqual(int(cells["hov"].sum()), 48)
                self.assertAlmostEqual(float(types["r_score"].iloc[0]), 2.0)

    def test_type_scores_apply_sampling_correction_and_weighted_mean(self):
        source = self.input_file("types.h5ad", cells=16, multiple_types=True)
        _, paths = self.prediction(source, n_crops=3)
        cells = pd.read_csv(paths[1])
        types = pd.read_csv(paths[2]).set_index("cell_type")
        self.assertEqual(int(cells["hov"].sum()), 48)
        weighted_score = 0.0
        for cell_type, group in cells.groupby("cell_type"):
            score = float(types.loc[cell_type, "r_score"])
            self.assertAlmostEqual(score, 2.0 * float(group["hov"].sum()) / (len(group) * 3))
            weighted_score += score * len(group) / len(cells)
        self.assertAlmostEqual(weighted_score, 2.0)

    def test_empty_input_is_rejected_without_outputs(self):
        source = self.input_file("empty.h5ad", cells=8, samples=0)
        with self.assertRaises(ValueError):
            self.prediction(source)
        self.assertFalse((self.root / "prediction.csv").exists())
        self.assertFalse((self.root / "prediction-cells.csv").exists())
        self.assertFalse((self.root / "prediction-types.csv").exists())

    def test_concurrent_prediction_has_one_owner_and_complete_csv(self):
        source = self.input_file("concurrent.h5ad", samples=20)
        barrier = threading.Barrier(2)
        original_read = core._read_h5ad

        def synchronized_read(*args, **kwargs):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            return original_read(*args, **kwargs)

        def release_rejected_call(future):
            if future.exception() is not None:
                barrier.abort()

        with patch.object(core, "_read_h5ad", side_effect=synchronized_read):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(self.prediction, source, interpret=False) for _ in range(2)]
                for future in futures:
                    future.add_done_callback(release_rejected_call)
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=20))
                    except (ValueError, FileExistsError) as error:
                        outcomes.append(error)
        successes = [value for value in outcomes if isinstance(value, tuple)]
        rejections = [value for value in outcomes if isinstance(value, (ValueError, FileExistsError))]
        self.assertEqual(len(successes), 1, f"Expected one output owner, got {len(successes)} successful calls")
        self.assertEqual(len(rejections), 1)
        frame = pd.read_csv(self.root / "prediction.csv")
        self.assertEqual(frame["sample_id"].tolist(), [f"patient-{index:02}" for index in range(20)])
        self.assertEqual(len(frame), 20)
        self.assertTrue(np.isfinite(frame["probability"]).all())
        self.assertEqual(successes[0][0]["n_samples"], 20)

    def test_training_write_failure_removes_both_outputs(self):
        source = self.input_file("train.h5ad", samples=20)
        model_path = self.root / "trained.pt"
        predictions_path = self.root / "test-predictions.csv"
        original_write = pd.DataFrame.to_csv

        def failed_write(frame, *args, **kwargs):
            original_write(frame, *args, **kwargs)
            raise OSError("Injected training CSV write failure")

        with patch.object(pd.DataFrame, "to_csv", new=failed_write):
            with self.assertRaises(OSError):
                self.train(source, model_path, predictions_path)
        self.assertEqual([path.name for path in (model_path, predictions_path) if path.exists()], [])

    def test_prediction_write_failure_removes_all_outputs(self):
        source = self.input_file("predict.h5ad")
        paths = [self.root / name for name in ("prediction.csv", "prediction-cells.csv", "prediction-types.csv")]
        original_write = pd.DataFrame.to_csv
        calls = 0

        def failed_second_write(frame, *args, **kwargs):
            nonlocal calls
            calls += 1
            original_write(frame, *args, **kwargs)
            if calls == 2:
                raise OSError("Injected cell-score CSV write failure")

        with patch.object(pd.DataFrame, "to_csv", new=failed_second_write):
            with self.assertRaises(OSError):
                self.prediction(source)
        self.assertEqual([path.name for path in paths if path.exists()], [])

    def test_training_existing_output_is_preserved_and_new_output_removed(self):
        source = self.input_file("train-existing.h5ad", samples=20)
        model_path = self.root / "trained.pt"
        predictions_path = self.root / "existing.csv"
        predictions_path.write_bytes(b"existing training output\n")
        with self.assertRaises((ValueError, FileExistsError)):
            self.train(source, model_path, predictions_path)
        self.assertEqual(predictions_path.read_bytes(), b"existing training output\n")
        self.assertFalse(model_path.exists())

    def test_prediction_existing_output_is_preserved_and_new_outputs_removed(self):
        source = self.input_file("predict-existing.h5ad")
        existing = self.root / "prediction-types.csv"
        existing.write_bytes(b"existing cell-type output\n")
        with self.assertRaises((ValueError, FileExistsError)):
            self.prediction(source)
        self.assertEqual(existing.read_bytes(), b"existing cell-type output\n")
        self.assertFalse((self.root / "prediction.csv").exists())
        self.assertFalse((self.root / "prediction-cells.csv").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
