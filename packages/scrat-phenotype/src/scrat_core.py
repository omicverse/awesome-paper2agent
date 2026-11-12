import copy
from contextlib import ExitStack, contextmanager
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import torch
from scipy import sparse
from sklearn.decomposition import PCA
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from torch import nn


def _input_file(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Input is not a readable file: {value}")
    return path


def _output_file(value, inputs):
    path = Path(value).expanduser().resolve()
    if path in inputs:
        raise ValueError("An output path must differ from every input path")
    if path.exists():
        raise ValueError(f"Output already exists: {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _exclusive_outputs(*paths):
    resources = ExitStack()
    created = []
    handles = {}
    try:
        for path in sorted(paths):
            try:
                handle = path.open("xb")
            except FileExistsError as error:
                raise ValueError(f"Output already exists: {path}") from error
            created.append(path)
            handles[path] = resources.enter_context(handle)
        yield handles
        resources.close()
    except BaseException as error:
        try:
            resources.close()
        except BaseException as cleanup_error:
            error.add_note(f"Could not close output files: {cleanup_error}")
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(f"Could not remove {path}: {cleanup_error}")
        raise


def _read_h5ad(path, sample_key, label_key, cell_type_key, embedding_key):
    data = ad.read_h5ad(path)
    if data.n_obs == 0:
        raise ValueError("Input must contain at least one cell")
    required = [sample_key, cell_type_key] + ([label_key] if label_key else [])
    missing = [key for key in required if key not in data.obs]
    if missing:
        raise ValueError(f"Missing obs column(s): {', '.join(missing)}")
    if embedding_key:
        if embedding_key not in data.obsm:
            raise ValueError(f"Missing obsm embedding: {embedding_key}")
        matrix = data.obsm[embedding_key]
        feature_names = [f"{embedding_key}:{i}" for i in range(matrix.shape[1])]
    else:
        matrix = data.X
        feature_names = [str(value) for value in data.var_names]
    if sparse.issparse(matrix):
        matrix = matrix.toarray()
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != data.n_obs or matrix.shape[1] < 2:
        raise ValueError("Expected a cells-by-features matrix with at least two features")
    if not np.isfinite(matrix).all():
        raise ValueError("Input features contain NaN or infinite values")
    obs = data.obs[required].copy()
    if obs.isna().any().any():
        raise ValueError("Sample, phenotype, and cell-type columns cannot contain missing values")
    samples = obs[sample_key].astype(str).to_numpy()
    cell_types = obs[cell_type_key].astype(str).to_numpy()
    labels = obs[label_key].astype(str).to_numpy() if label_key else None
    return matrix, samples, labels, cell_types, feature_names


def _sample_table(samples, labels, cell_types):
    frame = pd.DataFrame({"sample": samples, "label": labels, "cell_type": cell_types})
    conflicts = frame.groupby("sample", sort=True)["label"].nunique()
    if (conflicts > 1).any():
        bad = conflicts[conflicts > 1].index.tolist()
        raise ValueError(f"Each sample must have one phenotype; conflicting samples: {bad}")
    return frame


def _split_samples(sample_labels, test_fraction, validation_fraction, seed):
    if not 0 < test_fraction < 1 or not 0 < validation_fraction < 1 or test_fraction + validation_fraction >= 1:
        raise ValueError("Test and validation fractions must be positive and sum to less than one")
    ids = np.array(sorted(sample_labels))
    labels = np.array([sample_labels[value] for value in ids])
    if len(set(labels)) != 2:
        raise ValueError("ScRAT training requires exactly two phenotype classes")
    for offset in range(200):
        current_seed = seed + offset
        try:
            train_val, test = train_test_split(
                ids, test_size=test_fraction, random_state=current_seed, stratify=labels
            )
            remaining = ids[np.isin(ids, train_val)]
            remaining_labels = np.array([sample_labels[value] for value in remaining])
            relative_validation = validation_fraction / (1 - test_fraction)
            train, validation = train_test_split(
                remaining,
                test_size=relative_validation,
                random_state=current_seed + 1000,
                stratify=remaining_labels,
            )
        except ValueError:
            train_val, test = train_test_split(ids, test_size=test_fraction, random_state=current_seed)
            remaining = ids[np.isin(ids, train_val)]
            relative_validation = validation_fraction / (1 - test_fraction)
            train, validation = train_test_split(
                remaining, test_size=relative_validation, random_state=current_seed + 1000
            )
        if len(set(sample_labels[value] for value in train)) == 2 and len(validation) and len(test):
            return sorted(train), sorted(validation), sorted(test)
    raise ValueError("Could not create train/validation/test splits with both classes in training")


def _fit_pca(matrix, train_mask, n_pca, seed):
    n_components = min(n_pca, matrix.shape[1], int(train_mask.sum()) - 1)
    if n_components < 1:
        raise ValueError("Not enough training cells or features for PCA")
    pca = PCA(n_components=n_components, svd_solver="randomized", random_state=seed)
    pca.fit(matrix[train_mask])
    return pca.transform(matrix).astype(np.float32), pca


def _choose_pair(ids, sample_labels, mode, rng):
    if len(ids) < 2:
        raise ValueError("Mixup requires at least two training samples")
    if mode == "any":
        pair = rng.choice(ids, size=2, replace=False)
        return str(pair[0]), str(pair[1])
    by_label = {}
    for sample_id in ids:
        by_label.setdefault(sample_labels[sample_id], []).append(sample_id)
    if mode == "same":
        eligible = [values for values in by_label.values() if len(values) >= 2]
        if not eligible:
            raise ValueError("Same-phenotype mixup needs two samples in at least one class")
        group = eligible[int(rng.integers(len(eligible)))]
        pair = rng.choice(group, size=2, replace=False)
        return str(pair[0]), str(pair[1])
    if mode == "different":
        if len(by_label) != 2:
            raise ValueError("Different-phenotype mixup requires two classes")
        first, second = list(by_label.values())
        return str(rng.choice(first)), str(rng.choice(second))
    raise ValueError("mixup_mode must be 'any', 'same', or 'different'")


def _mixup_dataset(
    matrix,
    samples,
    labels,
    cell_types,
    train_ids,
    sample_labels,
    label_to_int,
    n_pseudo,
    mixup_cells,
    alpha,
    mode,
    seed,
):
    if n_pseudo < 1 or mixup_cells < 1 or alpha <= 0:
        raise ValueError("n_pseudo and mixup_cells must be positive; alpha must be greater than zero")
    rng = np.random.default_rng(seed)
    sample_indices = {value: np.flatnonzero(samples == value) for value in train_ids}
    pseudo_samples = []
    for _ in range(n_pseudo):
        first_id, second_id = _choose_pair(train_ids, sample_labels, mode, rng)
        first_idx = sample_indices[first_id]
        second_idx = sample_indices[second_id]
        lam = float(rng.beta(alpha, alpha))
        blocks = []
        block_types = []
        union = sorted(set(cell_types[first_idx]).union(cell_types[second_idx]))
        for cell_type in union:
            first_type = first_idx[cell_types[first_idx] == cell_type]
            second_type = second_idx[cell_types[second_idx] == cell_type]
            expected = mixup_cells * (
                lam * len(first_type) / len(first_idx)
                + (1 - lam) * len(second_type) / len(second_idx)
            )
            count = max(int(expected), 1)
            if len(first_type) and len(second_type):
                first_cells = matrix[rng.choice(first_type, count, replace=True)]
                second_cells = matrix[rng.choice(second_type, count, replace=True)]
                mixed = lam * first_cells + (1 - lam) * second_cells
            elif len(first_type):
                cells = matrix[rng.choice(first_type, count, replace=True)]
                mixed = lam * cells + rng.normal(0, 1e-5 ** 0.5, cells.shape)
            else:
                cells = matrix[rng.choice(second_type, count, replace=True)]
                mixed = (1 - lam) * cells + rng.normal(0, 1e-5 ** 0.5, cells.shape)
            blocks.append(mixed.astype(np.float32))
            block_types.extend([cell_type] * count)
        soft_label = lam * label_to_int[sample_labels[first_id]] + (1 - lam) * label_to_int[
            sample_labels[second_id]
        ]
        pseudo_samples.append(
            {
                "X": np.concatenate(blocks).astype(np.float32),
                "y": np.float32(soft_label),
                "cell_types": np.asarray(block_types, dtype=str),
                "sources": (first_id, second_id),
                "lambda": lam,
            }
        )
    return pseudo_samples


class ScRATModel(nn.Module):
    def __init__(self, input_dim, model_dim=128, heads=8, dropout=0.3):
        super().__init__()
        if model_dim % heads:
            raise ValueError("model_dim must be divisible by heads")
        self.model_dim = model_dim
        self.heads = heads
        self.head_dim = model_dim // heads
        self.input_net = nn.Linear(input_dim, model_dim)
        self.qkv = nn.Linear(model_dim, 3 * model_dim)
        self.attention_output = nn.Linear(model_dim, model_dim)
        self.feed_forward = nn.Sequential(
            nn.Linear(model_dim, 2 * model_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(2 * model_dim, model_dim),
        )
        self.norm1 = nn.LayerNorm(model_dim)
        self.norm2 = nn.LayerNorm(model_dim)
        self.dropout = nn.Dropout(dropout)
        self.attention_dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(model_dim, 1)

    def forward(self, value, return_attention=False):
        batch, cells, _ = value.shape
        value = self.input_net(value)
        qkv = self.qkv(value).reshape(batch, cells, self.heads, 3 * self.head_dim)
        qkv = qkv.permute(0, 2, 1, 3)
        query, key, val = qkv.chunk(3, dim=-1)
        logits = torch.matmul(query, key.transpose(-2, -1)) / np.sqrt(self.head_dim)
        attention = torch.softmax(logits, dim=-1)
        dropped_attention = self.attention_dropout(attention)
        attended = torch.matmul(dropped_attention, val)
        attended = attended.permute(0, 2, 1, 3).reshape(batch, cells, self.model_dim)
        value = self.norm1(value + self.dropout(self.attention_output(attended)))
        value = self.norm2(value + self.dropout(self.feed_forward(value)))
        prediction = self.classifier(value.mean(dim=1)).squeeze(-1)
        if return_attention:
            return prediction, attention
        return prediction


def _one_crop(matrix, crop_cells, rng):
    if matrix.shape[0] < crop_cells:
        raise ValueError(f"A sample has {matrix.shape[0]} cells but crop_cells is {crop_cells}")
    indices = rng.choice(matrix.shape[0], crop_cells, replace=False)
    return matrix[indices], indices


def _crops(matrix, n_crops, crop_cells, seed):
    rng = np.random.default_rng(seed)
    return np.stack([_one_crop(matrix, crop_cells, rng)[0] for _ in range(n_crops)])


def _resolve_device(value):
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is not available")
    return device


def _batch_predictions(model, crops, device, batch_size):
    probabilities = []
    with torch.no_grad():
        for start in range(0, len(crops), batch_size):
            batch = torch.from_numpy(crops[start : start + batch_size]).float().to(device)
            probabilities.extend(torch.sigmoid(model(batch)).cpu().numpy().tolist())
    return np.asarray(probabilities, dtype=np.float64)


def mixup_h5ad(
    h5ad_path,
    output_path,
    sample_key="patient_id",
    label_key="Outcome",
    cell_type_key="cell_type",
    embedding_key="X_pca",
    n_pca=50,
    n_pseudo=300,
    mixup_cells=10000,
    min_cells=500,
    alpha=0.5,
    mixup_mode="different",
    seed=240,
):
    source = _input_file(h5ad_path)
    destination = _output_file(output_path, {source})
    matrix, samples, labels, cell_types, _ = _read_h5ad(
        source, sample_key, label_key, cell_type_key, embedding_key
    )
    frame = _sample_table(samples, labels, cell_types)
    classes = sorted(str(value) for value in frame["label"].unique())
    if len(classes) != 2:
        raise ValueError("ScRAT mixup requires exactly two phenotype classes")
    label_to_int = {value: index for index, value in enumerate(classes)}
    sample_labels = frame.groupby("sample", sort=True)["label"].first().to_dict()
    counts = frame.groupby("sample").size()
    train_ids = sorted(value for value in sample_labels if counts[value] >= min_cells)
    excluded = sorted(set(sample_labels) - set(train_ids))
    if embedding_key:
        transformed = matrix
    else:
        transformed, _ = _fit_pca(matrix, np.ones(matrix.shape[0], dtype=bool), n_pca, seed)
    pseudo = _mixup_dataset(
        transformed,
        samples,
        labels,
        cell_types,
        train_ids,
        sample_labels,
        label_to_int,
        n_pseudo,
        mixup_cells,
        alpha,
        mixup_mode,
        seed,
    )
    with _exclusive_outputs(destination) as files:
        np.savez_compressed(
            files[destination],
            X=np.concatenate([item["X"] for item in pseudo]),
            sample_index=np.concatenate(
                [np.full(len(item["X"]), index, dtype=np.int64) for index, item in enumerate(pseudo)]
            ),
            labels=np.asarray([item["y"] for item in pseudo], dtype=np.float32),
            lambdas=np.asarray([item["lambda"] for item in pseudo], dtype=np.float64),
            cell_types=np.concatenate([item["cell_types"] for item in pseudo]).astype(str),
            source_samples=np.asarray([item["sources"] for item in pseudo], dtype=str),
            label_classes=np.asarray(classes, dtype=str),
        )
    return {
        "n_pseudo_samples": n_pseudo,
        "n_cells": int(sum(len(item["X"]) for item in pseudo)),
        "n_features": int(transformed.shape[1]),
        "label_classes": classes,
        "excluded_samples": excluded,
        "output_path": str(destination),
        "artifacts": [str(destination)],
    }


def train_h5ad(
    h5ad_path,
    model_path,
    test_predictions_path=None,
    sample_key="patient_id",
    label_key="Outcome",
    cell_type_key="cell_type",
    embedding_key="X_pca",
    n_pca=50,
    test_fraction=0.2,
    validation_fraction=0.2,
    min_cells=500,
    n_pseudo=300,
    mixup_cells=10000,
    alpha=0.5,
    mixup_mode="different",
    crop_cells=500,
    train_crops=20,
    test_crops=50,
    model_dim=128,
    heads=8,
    dropout=0.3,
    epochs=100,
    batch_size=64,
    learning_rate=0.01,
    weight_decay=0.0001,
    device="cpu",
    seed=240,
):
    source = _input_file(h5ad_path)
    destination = _output_file(model_path, {source})
    if test_predictions_path is None:
        test_predictions_path = str(destination.with_name(destination.stem + "_test_predictions.csv"))
    prediction_destination = _output_file(test_predictions_path, {source, destination})
    if epochs < 1 or train_crops < 1 or test_crops < 1 or batch_size < 1 or crop_cells < 1:
        raise ValueError("Epoch, crop, and batch counts must be positive")
    if min_cells < crop_cells:
        raise ValueError("min_cells must be at least crop_cells")
    matrix, samples, labels, cell_types, feature_names = _read_h5ad(
        source, sample_key, label_key, cell_type_key, embedding_key
    )
    frame = _sample_table(samples, labels, cell_types)
    classes = sorted(str(value) for value in frame["label"].unique())
    if len(classes) != 2:
        raise ValueError("This package reproduces binary ScRAT classification only")
    label_to_int = {value: index for index, value in enumerate(classes)}
    sample_labels = frame.groupby("sample", sort=True)["label"].first().to_dict()
    counts = frame.groupby("sample").size()
    eligible = sorted(value for value in sample_labels if counts[value] >= min_cells)
    excluded = sorted(set(sample_labels) - set(eligible))
    eligible_labels = {value: sample_labels[value] for value in eligible}
    train_ids, validation_ids, test_ids = _split_samples(
        eligible_labels, test_fraction, validation_fraction, seed
    )
    train_mask = np.isin(samples, train_ids)
    if embedding_key:
        transformed = matrix
        pca = None
    else:
        transformed, pca = _fit_pca(matrix, train_mask, n_pca, seed)
    pseudo = _mixup_dataset(
        transformed,
        samples,
        labels,
        cell_types,
        train_ids,
        sample_labels,
        label_to_int,
        n_pseudo,
        mixup_cells,
        alpha,
        mixup_mode,
        seed + 10,
    )
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed + 20)
    resolved_device = _resolve_device(device)
    model = ScRATModel(transformed.shape[1], model_dim, heads, dropout).to(resolved_device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=50, gamma=0.5)
    criterion = nn.BCEWithLogitsLoss()
    validation_crops = [
        _crops(transformed[samples == value], test_crops, crop_cells, seed + 100 + index)
        for index, value in enumerate(validation_ids)
    ]
    validation_labels = np.concatenate(
        [
            np.full(test_crops, label_to_int[sample_labels[value]], dtype=np.float32)
            for value in validation_ids
        ]
    )
    best_loss = float("inf")
    best_state = None
    best_epoch = 0
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        pairs = [(pseudo_index, crop_index) for pseudo_index in range(n_pseudo) for crop_index in range(train_crops)]
        rng.shuffle(pairs)
        losses = []
        for start in range(0, len(pairs), batch_size):
            batch_pairs = pairs[start : start + batch_size]
            batch_x = np.stack([_one_crop(pseudo[index]["X"], crop_cells, rng)[0] for index, _ in batch_pairs])
            batch_y = torch.from_numpy(
                np.asarray([pseudo[index]["y"] for index, _ in batch_pairs], dtype=np.float32)
            ).to(resolved_device)
            optimizer.zero_grad()
            logits = model(torch.from_numpy(batch_x).float().to(resolved_device))
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step()
        model.eval()
        all_validation = np.concatenate(validation_crops)
        with torch.no_grad():
            validation_logits = []
            for start in range(0, len(all_validation), batch_size):
                batch = torch.from_numpy(all_validation[start : start + batch_size]).float().to(resolved_device)
                validation_logits.extend(model(batch).cpu().numpy().tolist())
            validation_loss = float(
                criterion(
                    torch.from_numpy(np.asarray(validation_logits, dtype=np.float32)),
                    torch.from_numpy(validation_labels),
                )
            )
        train_loss = float(np.mean(losses))
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss})
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = copy.deepcopy({key: value.cpu() for key, value in model.state_dict().items()})
    model.load_state_dict(best_state)
    model.to(resolved_device).eval()
    test_probabilities = []
    test_predictions = []
    test_vote_fractions = []
    for index, sample_id in enumerate(test_ids):
        crops = _crops(transformed[samples == sample_id], test_crops, crop_cells, seed + 200 + index)
        probabilities = _batch_predictions(model, crops, resolved_device, batch_size)
        vote_fraction = float(np.mean(probabilities > 0.5))
        test_probabilities.append(float(probabilities.mean()))
        test_vote_fractions.append(vote_fraction)
        test_predictions.append(int(vote_fraction > 0.5))
    test_true = [label_to_int[sample_labels[value]] for value in test_ids]
    test_auc = float(roc_auc_score(test_true, test_probabilities)) if len(set(test_true)) == 2 else None
    test_accuracy = float(accuracy_score(test_true, test_predictions))
    artifact = {
        "format_version": 1,
        "state_dict": {key: value.cpu() for key, value in model.state_dict().items()},
        "model_config": {
            "input_dim": int(transformed.shape[1]),
            "model_dim": model_dim,
            "heads": heads,
            "dropout": dropout,
        },
        "sample_key": sample_key,
        "cell_type_key": cell_type_key,
        "embedding_key": embedding_key,
        "feature_names": feature_names,
        "label_classes": classes,
        "crop_cells": crop_cells,
        "pca_mean": torch.from_numpy(pca.mean_.astype(np.float32)) if pca else None,
        "pca_components": torch.from_numpy(pca.components_.astype(np.float32)) if pca else None,
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "training_history": history,
    }
    prediction_frame = pd.DataFrame(
        {
            "sample_id": test_ids,
            "true_label": [sample_labels[value] for value in test_ids],
            "predicted_label": [classes[value] for value in test_predictions],
            "probability": test_probabilities,
            "positive_vote_fraction": test_vote_fractions,
            "positive_class": classes[1],
        }
    )
    with _exclusive_outputs(destination, prediction_destination) as files:
        torch.save(artifact, files[destination])
        prediction_frame.to_csv(files[prediction_destination], index=False)
    return {
        "n_train_samples": len(train_ids),
        "n_validation_samples": len(validation_ids),
        "n_test_samples": len(test_ids),
        "n_pseudo_samples": n_pseudo,
        "n_features": int(transformed.shape[1]),
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "test_accuracy": test_accuracy,
        "test_auc": test_auc,
        "label_classes": classes,
        "positive_class": classes[1],
        "excluded_samples": excluded,
        "model_path": str(destination),
        "test_predictions_path": str(prediction_destination),
        "artifacts": [str(destination), str(prediction_destination)],
    }


def predict_h5ad(
    h5ad_path,
    model_path,
    output_path,
    cell_scores_path=None,
    cell_type_scores_path=None,
    n_crops=50,
    crop_cells=0,
    top_k=1,
    batch_size=16,
    device="cpu",
    seed=240,
):
    source = _input_file(h5ad_path)
    model_source = _input_file(model_path)
    destination = _output_file(output_path, {source, model_source})
    interpret = cell_scores_path is not None or cell_type_scores_path is not None
    if interpret and (cell_scores_path is None or cell_type_scores_path is None):
        raise ValueError("Provide both cell_scores_path and cell_type_scores_path, or neither")
    cell_destination = None
    type_destination = None
    if interpret:
        cell_destination = _output_file(cell_scores_path, {source, model_source, destination})
        type_destination = _output_file(
            cell_type_scores_path, {source, model_source, destination, cell_destination}
        )
    if n_crops < 1 or batch_size < 1 or top_k < 1:
        raise ValueError("n_crops, batch_size, and top_k must be positive")
    artifact = torch.load(model_source, map_location="cpu", weights_only=True)
    if artifact.get("format_version") != 1:
        raise ValueError("Unsupported ScRAT model artifact")
    matrix, samples, _, cell_types, feature_names = _read_h5ad(
        source,
        artifact["sample_key"],
        None,
        artifact["cell_type_key"],
        artifact["embedding_key"],
    )
    if feature_names != artifact["feature_names"]:
        raise ValueError("Prediction features or their order differ from the training artifact")
    if artifact["pca_mean"] is not None:
        matrix = ((matrix - artifact["pca_mean"].numpy()) @ artifact["pca_components"].numpy().T).astype(
            np.float32
        )
    crop_cells = crop_cells or artifact["crop_cells"]
    if top_k > crop_cells:
        raise ValueError("top_k cannot exceed crop_cells")
    config = artifact["model_config"]
    resolved_device = _resolve_device(device)
    model = ScRATModel(**config).to(resolved_device)
    model.load_state_dict(artifact["state_dict"])
    model.eval()
    classes = artifact["label_classes"]
    rows = []
    cell_rows = []
    type_rows = []
    for sample_index, sample_id in enumerate(sorted(set(samples))):
        sample_indices = np.flatnonzero(samples == sample_id)
        sample_matrix = matrix[sample_indices]
        sample_types = cell_types[sample_indices]
        if len(sample_indices) < crop_cells:
            raise ValueError(
                f"Sample {sample_id} has {len(sample_indices)} cells but crop_cells is {crop_cells}"
            )
        crops = _crops(sample_matrix, n_crops, crop_cells, seed + sample_index)
        probabilities = _batch_predictions(model, crops, resolved_device, batch_size)
        probability = float(probabilities.mean())
        vote_fraction = float(np.mean(probabilities > 0.5))
        predicted = int(vote_fraction > 0.5)
        rows.append(
            {
                "sample_id": sample_id,
                "predicted_label": classes[predicted],
                "probability": probability,
                "positive_vote_fraction": vote_fraction,
                "positive_class": classes[1],
            }
        )
        if interpret:
            rng = np.random.default_rng(seed + sample_index)
            hov = np.zeros(len(sample_indices), dtype=np.int64)
            appearances = np.zeros(len(sample_indices), dtype=np.int64)
            type_names = sorted(set(sample_types))
            type_totals = {value: int(np.sum(sample_types == value)) for value in type_names}
            r_score_sum = dict.fromkeys(type_names, 0.0)
            for _ in range(n_crops):
                _, local_indices = _one_crop(sample_matrix, crop_cells, rng)
                batch = torch.from_numpy(sample_matrix[local_indices][None, ...]).float().to(resolved_device)
                with torch.no_grad():
                    _, attention = model(batch, return_attention=True)
                attention = attention[0].cpu().numpy()
                threshold = np.partition(attention, -top_k, axis=-1)[..., -top_k, None]
                counts = np.count_nonzero(attention >= threshold, axis=(0, 1))
                hov[local_indices] += counts
                appearances[local_indices] += 1
                for type_name in type_names:
                    local_type = sample_types[local_indices] == type_name
                    r_score_sum[type_name] += float(counts[local_type].sum()) / type_totals[type_name]
            for local_index, global_index in enumerate(sample_indices):
                cell_rows.append(
                    {
                        "sample_id": sample_id,
                        "cell_index": int(global_index),
                        "cell_type": str(sample_types[local_index]),
                        "hov": int(hov[local_index]),
                        "n_appearances": int(appearances[local_index]),
                        "hov_mean": float(hov[local_index] / appearances[local_index])
                        if appearances[local_index]
                        else 0.0,
                    }
                )
            for type_name in type_names:
                type_rows.append(
                    {
                        "sample_id": sample_id,
                        "predicted_label": classes[predicted],
                        "probability": probability,
                        "cell_type": type_name,
                        "r_score": r_score_sum[type_name] / n_crops * len(sample_indices) / crop_cells,
                    }
                )
    frames = {destination: pd.DataFrame(rows)}
    if interpret:
        frames[cell_destination] = pd.DataFrame(cell_rows)
        frames[type_destination] = pd.DataFrame(type_rows)
    with _exclusive_outputs(*frames) as files:
        for path, frame in frames.items():
            frame.to_csv(files[path], index=False)
    return {
        "n_samples": len(rows),
        "n_crops_per_sample": n_crops,
        "crop_cells": crop_cells,
        "positive_class": classes[1],
        "predictions_path": str(destination),
        "cell_scores_path": str(cell_destination) if cell_destination else None,
        "cell_type_scores_path": str(type_destination) if type_destination else None,
        "artifacts": [str(path) for path in frames],
    }
