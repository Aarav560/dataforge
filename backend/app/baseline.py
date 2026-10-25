"""Quick baseline models on a dataset version (the "train a baseline" button).

Builds features from numeric, fixed-length vector, categorical, text and image
columns, trains a fast scikit-learn model, and reports metrics next to a
trivial predictor so you can tell whether the dataset carries signal.
"""

import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

TARGET_GUESSES = ("label", "answer", "action", "target", "class", "y", "category")
# Never used as features: bookkeeping, identifiers, or leaking the label.
SKIP = {"split", "needs_review", "label_confidence", "label_source", "asset_id", "path", "url", "source_file",
        "video", "frame", "timestamp", "game", "step", "chunk", "next_state", "pii", "fetched_at", "title"}
# Columns that are outcomes of an action, or other parts of the same action.
# They are suggested as exclusions because they would leak the answer.
OUTCOMES = {"reward", "lines", "done", "return", "next_state", "score_delta", "terminal"}
ACTION_GROUPS = [{"rotation", "column"}, {"action", "piece_action"}, {"label", "label_auto"}]
MAX_ROWS = 200_000
MAX_VECTOR = 1024
MAX_IMAGES = 5000
IMAGE_SIDE = 16


class BaselineError(Exception):
    pass


def target_candidates(schema: list[dict]) -> list[str]:
    names = [c["name"] for c in schema if c["name"] not in SKIP and not c["type"].startswith(("list", "struct"))]
    guessed = [n for n in TARGET_GUESSES if n in names]
    return guessed + [n for n in names if n not in guessed]


def suggested_exclusions(columns: list[str], target: str) -> list[str]:
    out = [c for c in columns if c in OUTCOMES and c != target]
    for group in ACTION_GROUPS:
        if target in group:
            out += [c for c in columns if c in group and c != target]
    return sorted(set(out))


def _is_numeric(t: pa.DataType) -> bool:
    return pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_boolean(t)


def _features(table: pa.Table, target: str, asset_paths: dict[int, Path], exclude: set[str] = frozenset()):
    from sklearn.feature_extraction.text import TfidfVectorizer

    blocks, used, sparse_used = [], [], False
    n = table.num_rows
    text_col, text_len = None, 0
    for f in table.schema:
        name = f.name
        if name == target or name in SKIP or name in exclude:
            continue
        col = table.column(name)
        if _is_numeric(f.type):
            x = np.array(col.cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=float)
            x[np.isnan(x)] = np.nanmedian(x) if np.isfinite(np.nanmedian(x)) else 0.0
            blocks.append(x.reshape(-1, 1))
            used.append({"column": name, "as": "numeric"})
        elif (pa.types.is_list(f.type) or pa.types.is_large_list(f.type)) and _is_numeric(f.type.value_type):
            lengths = set(pc_len for pc_len in (len(v) if v is not None else -1 for v in col.to_pylist()))
            if len(lengths) != 1 or -1 in lengths or not 0 < next(iter(lengths)) <= MAX_VECTOR:
                continue
            blocks.append(np.array(col.to_pylist(), dtype=float))
            used.append({"column": name, "as": f"vector[{next(iter(lengths))}]"})
        elif pa.types.is_string(f.type) or pa.types.is_large_string(f.type):
            values = ["" if v is None else str(v) for v in col.to_pylist()]
            distinct = len(set(values))
            if distinct <= 50 and distinct < max(2, 0.5 * n):
                cats = sorted(set(values))
                onehot = np.zeros((n, len(cats)))
                idx = {c: i for i, c in enumerate(cats)}
                for i, v in enumerate(values):
                    onehot[i, idx[v]] = 1
                blocks.append(onehot)
                used.append({"column": name, "as": f"one-hot[{len(cats)}]"})
            else:
                avg = sum(map(len, values)) / max(n, 1)
                if avg > text_len:
                    text_col, text_len = name, avg
    if text_col is not None:
        values = ["" if v is None else str(v) for v in table.column(text_col).to_pylist()]
        tfidf = TfidfVectorizer(max_features=5000, ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        blocks.append(tfidf.fit_transform(values))
        used.append({"column": text_col, "as": "tf-idf"})
        sparse_used = True
    if {"asset_id", "path"} <= set(table.column_names) and asset_paths:
        from PIL import Image

        if n > MAX_IMAGES:
            raise BaselineError(f"image baselines are capped at {MAX_IMAGES} rows; sample first")
        pixels = np.zeros((n, IMAGE_SIDE * IMAGE_SIDE))
        for i, (aid, rel) in enumerate(zip(table.column("asset_id").to_pylist(), table.column("path").to_pylist())):
            root = asset_paths.get(aid)
            if root is None:
                continue
            with Image.open(root / rel) as im:
                pixels[i] = np.asarray(im.convert("L").resize((IMAGE_SIDE, IMAGE_SIDE)), dtype=float).ravel() / 255.0
        blocks.append(pixels)
        used.append({"column": "path", "as": f"pixels[{IMAGE_SIDE}x{IMAGE_SIDE}]"})
    if not blocks:
        raise BaselineError("no usable feature columns (numeric, vectors, categories, text or images)")
    if sparse_used:
        X = sparse.hstack([b if sparse.issparse(b) else sparse.csr_matrix(b) for b in blocks]).tocsr()
    else:
        X = np.hstack(blocks)
    return X, used, sparse_used


def _split(table: pa.Table, y: np.ndarray, classification: bool, seed: int = 0):
    n = table.num_rows
    if "split" in table.column_names:
        split = np.array([str(s) for s in table.column("split").to_pylist()])
        train, test = split == "train", np.isin(split, ["test", "val"])
        if train.sum() and test.sum():
            # Prefer the test split; fall back to validation when there is no test split.
            if (split == "test").sum():
                test = split == "test"
            return np.flatnonzero(train), np.flatnonzero(test), "split column"
    from sklearn.model_selection import train_test_split

    idx = np.arange(n)
    counts = np.unique(y, return_counts=True)[1] if classification else None
    strat = y if classification and counts.min() >= 2 else None
    tr, te = train_test_split(idx, test_size=0.2, random_state=seed, stratify=strat)
    return tr, te, "random 80/20" + (" (stratified)" if strat is not None else "")


def train(path: Path, target: str, asset_paths: dict[int, Path], model_out: Path | None = None,
          exclude: list[str] | None = None) -> dict:
    """Train and evaluate. `exclude=None` applies the suggested leakage exclusions."""
    from sklearn import metrics
    from sklearn.ensemble import (
        HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor,
    )
    from sklearn.linear_model import LogisticRegression, Ridge

    started = time.time()
    table = pq.read_table(path)
    if target not in table.column_names:
        raise BaselineError(f"no column {target!r}")
    # Drop rows without a target, then cap size.
    mask = np.array([v is not None and v != "" for v in table.column(target).to_pylist()])
    table = table.filter(pa.array(mask))
    if table.num_rows > MAX_ROWS:
        table = table.slice(0, MAX_ROWS)
    if table.num_rows < 10:
        raise BaselineError("need at least 10 labeled rows")
    t_type = table.schema.field(target).type
    raw_y = table.column(target).to_pylist()
    distinct = len(set(raw_y))
    classification = not (_is_numeric(t_type) and not pa.types.is_boolean(t_type) and distinct > 20)
    y = np.array([str(v) for v in raw_y]) if classification else np.array(raw_y, dtype=float)
    if classification and distinct < 2:
        raise BaselineError("the target has a single class")
    excluded = suggested_exclusions(table.column_names, target) if exclude is None else sorted(set(exclude))
    X, used, is_sparse = _features(table, target, asset_paths, set(excluded))
    tr, te, split_how = _split(table, y, classification)

    # Histogram boosting needs ~20 rows per leaf; small sets get a random forest.
    small = len(tr) < 500
    if is_sparse:
        model = LogisticRegression(max_iter=2000) if classification else Ridge()
        name = "logistic regression (tf-idf)" if classification else "ridge regression (tf-idf)"
    elif small:
        model = (RandomForestClassifier if classification else RandomForestRegressor)(n_estimators=200, random_state=0)
        name = "random forest"
    else:
        # Capped with early stopping: this is a quick signal check, not a tuned model.
        kw = dict(max_iter=100, early_stopping=True, validation_fraction=0.1, n_iter_no_change=20, random_state=0)
        model = HistGradientBoostingClassifier(**kw) if classification else HistGradientBoostingRegressor(**kw)
        name = "gradient-boosted trees"
    model.fit(X[tr], y[tr])
    pred = model.predict(X[te])

    result: dict = {
        "task": "classification" if classification else "regression",
        "model": name,
        "target": target,
        "rows": {"train": int(len(tr)), "test": int(len(te))},
        "split": split_how,
        "features": used,
        "excluded": excluded,
    }
    if classification:
        values, counts = np.unique(y[tr], return_counts=True)
        majority = values[np.argmax(counts)]
        labels = sorted(set(y))
        result["metrics"] = {
            "accuracy": float(metrics.accuracy_score(y[te], pred)),
            "macro_f1": float(metrics.f1_score(y[te], pred, average="macro", zero_division=0)),
            "baseline_accuracy": float(np.mean(y[te] == majority)),
            "baseline": f"always '{majority}'",
        }
        report = metrics.classification_report(y[te], pred, output_dict=True, zero_division=0)
        result["per_class"] = [
            {"label": k, "precision": v["precision"], "recall": v["recall"], "f1": v["f1-score"], "support": int(v["support"])}
            for k, v in report.items() if k in labels
        ][:30]
        if len(labels) <= 20:
            result["confusion"] = {"labels": labels, "matrix": metrics.confusion_matrix(y[te], pred, labels=labels).tolist()}
    else:
        mean = float(np.mean(y[tr]))
        result["metrics"] = {
            "mae": float(metrics.mean_absolute_error(y[te], pred)),
            "rmse": float(np.sqrt(metrics.mean_squared_error(y[te], pred))),
            "r2": float(metrics.r2_score(y[te], pred)),
            "baseline_mae": float(metrics.mean_absolute_error(y[te], np.full(len(te), mean))),
            "baseline": "always the training mean",
        }
    if model_out is not None:
        import joblib

        model_out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "target": target, "features": used, "task": result["task"]}, model_out)
    result["seconds"] = round(time.time() - started, 2)
    return result
