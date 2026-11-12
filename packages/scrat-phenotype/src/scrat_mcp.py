from fastmcp import FastMCP

from scrat_core import mixup_h5ad, predict_h5ad, train_h5ad

mcp = FastMCP(
    "ScRAT phenotype prediction",
    instructions=(
        "Predict binary sample phenotypes from single-cell RNA-seq matrices with "
        "cell-type-aware mixup, multi-head attention, and attention-based cell scores."
    ),
)


@mcp.tool()
def scrat_mixup(
    h5ad_path: str,
    output_path: str,
    sample_key: str = "patient_id",
    label_key: str = "Outcome",
    cell_type_key: str = "cell_type",
    embedding_key: str | None = "X_pca",
    n_pca: int = 50,
    n_pseudo: int = 300,
    mixup_cells: int = 10000,
    min_cells: int = 500,
    alpha: float = 0.5,
    mixup_mode: str = "different",
    seed: int = 240,
) -> dict:
    """Generate cell-type-aware ScRAT pseudo-samples from an H5AD into a compressed NPZ."""
    return mixup_h5ad(
        h5ad_path,
        output_path,
        sample_key,
        label_key,
        cell_type_key,
        embedding_key,
        n_pca,
        n_pseudo,
        mixup_cells,
        min_cells,
        alpha,
        mixup_mode,
        seed,
    )


@mcp.tool()
def scrat_train(
    h5ad_path: str,
    model_path: str,
    test_predictions_path: str | None = None,
    sample_key: str = "patient_id",
    label_key: str = "Outcome",
    cell_type_key: str = "cell_type",
    embedding_key: str | None = "X_pca",
    n_pca: int = 50,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.2,
    min_cells: int = 500,
    n_pseudo: int = 300,
    mixup_cells: int = 10000,
    alpha: float = 0.5,
    mixup_mode: str = "different",
    crop_cells: int = 500,
    train_crops: int = 20,
    test_crops: int = 50,
    model_dim: int = 128,
    heads: int = 8,
    dropout: float = 0.3,
    epochs: int = 100,
    batch_size: int = 64,
    learning_rate: float = 0.01,
    weight_decay: float = 0.0001,
    device: str = "cpu",
    seed: int = 240,
) -> dict:
    """Train binary ScRAT with patient-level splits and save the model and held-out predictions."""
    return train_h5ad(
        h5ad_path,
        model_path,
        test_predictions_path,
        sample_key,
        label_key,
        cell_type_key,
        embedding_key,
        n_pca,
        test_fraction,
        validation_fraction,
        min_cells,
        n_pseudo,
        mixup_cells,
        alpha,
        mixup_mode,
        crop_cells,
        train_crops,
        test_crops,
        model_dim,
        heads,
        dropout,
        epochs,
        batch_size,
        learning_rate,
        weight_decay,
        device,
        seed,
    )


@mcp.tool()
def scrat_predict(
    h5ad_path: str,
    model_path: str,
    output_path: str,
    cell_scores_path: str | None = None,
    cell_type_scores_path: str | None = None,
    n_crops: int = 50,
    crop_cells: int = 0,
    top_k: int = 1,
    batch_size: int = 16,
    device: str = "cpu",
    seed: int = 240,
) -> dict:
    """Predict sample phenotypes and optionally write per-cell HOV and per-cell-type R-scores."""
    return predict_h5ad(
        h5ad_path,
        model_path,
        output_path,
        cell_scores_path,
        cell_type_scores_path,
        n_crops,
        crop_cells,
        top_k,
        batch_size,
        device,
        seed,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
