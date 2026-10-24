"""'Train a baseline' (plan phase 5): quick numpy softmax-regression / ridge baseline on a dataset version."""
from __future__ import annotations

import numpy as np
import polars as pl

TARGETS = ("label", "action", "target", "class", "y", "category", "answer", "column", "reward")


def _image_features(paths: list[str]) -> np.ndarray:
    """Cheap, model-free image features: HSV colour histogram + an 8×8 grey thumbnail.
    Enough to tell whether labels are learnable (e.g. leaf disease colour/texture), not a real classifier."""
    import cv2
    out = np.zeros((len(paths), 8 * 4 * 4 + 64))
    for i, p in enumerate(paths):
        im = cv2.imread(p, cv2.IMREAD_REDUCED_COLOR_4)
        if im is None:
            continue
        im = cv2.resize(im, (64, 64), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
        g = cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (8, 8), interpolation=cv2.INTER_AREA).flatten() / 255.0
        out[i] = np.r_[h / max(1.0, h.sum()), g]
    return out


def _features(df: pl.DataFrame, target: str) -> tuple[np.ndarray, list[str]]:
    if "image_path" in df.columns:
        X = _image_features(df["image_path"].to_list())
        mu, sd = X.mean(0), X.std(0) + 1e-8
        return (X - mu) / sd, [f"hsv_hist[{i}]" for i in range(128)] + [f"thumb[{i}]" for i in range(64)]
    feats, names = [], []
    for c, t in df.schema.items():
        if c in (target, "split") or c.startswith("_"):
            continue
        s = df[c]
        if t.is_numeric() or t == pl.Boolean:
            feats.append(s.cast(pl.Float64).fill_null(0).to_numpy()); names.append(c)
        elif t == pl.String:
            sample = s.drop_nulls().head(1)
            if sample.len() and set(sample[0]) <= {"0", "1"} and 16 <= len(sample[0]) <= 1024:
                # bit-string state (e.g. a Tetris board) → expand to bit features
                L = len(sample[0])
                bits = np.array([[ch == "1" for ch in (v or "0" * L)[:L].ljust(L, "0")] for v in s.to_list()], dtype=np.float64)
                feats += list(bits.T); names += [f"{c}[{i}]" for i in range(L)]
            elif 1 < s.n_unique() <= 30:
                for v in s.drop_nulls().unique().to_list()[:30]:
                    feats.append((s == v).fill_null(False).cast(pl.Float64).to_numpy()); names.append(f"{c}={v}")
    if not feats:
        raise RuntimeError("no usable feature columns (need numeric, low-cardinality text, or bit-string state columns)")
    X = np.column_stack(feats)
    mu, sd = X.mean(0), X.std(0) + 1e-8
    return (X - mu) / sd, names


def train(df: pl.DataFrame, target: str | None = None, epochs: int = 200, max_rows: int = 200_000) -> dict:
    target = target or next((t for t in TARGETS if t in df.columns), None)
    if not target or target not in df.columns:
        raise RuntimeError(f"pick a target column (none of {TARGETS} found)")
    df = df.filter(pl.col(target).is_not_null())
    if "image_path" in df.columns:
        max_rows = min(max_rows, 6000)  # image decoding dominates; a sample is enough for a signal check
    if df.height > max_rows:
        df = df.sample(max_rows, seed=0)
    if "split" in df.columns and df.filter(pl.col("split") == "train").height and df.filter(pl.col("split") != "train").height:
        tr_mask = (df["split"] == "train").to_numpy()
    else:
        tr_mask = np.random.default_rng(0).random(df.height) < 0.8
    X, names = _features(df, target)
    y_raw = df[target]
    classification = not y_raw.dtype.is_float() and y_raw.n_unique() <= 100
    Xtr, Xte = X[tr_mask], X[~tr_mask]
    if classification:
        classes = sorted(map(str, y_raw.unique().to_list()))
        idx = {c: i for i, c in enumerate(classes)}
        y = np.array([idx[str(v)] for v in y_raw.to_list()])
        ytr, yte = y[tr_mask], y[~tr_mask]
        W = np.zeros((X.shape[1], len(classes))); b = np.zeros(len(classes))
        lr = 0.5
        Y = np.eye(len(classes))[ytr]
        for _ in range(epochs):
            z = Xtr @ W + b
            z -= z.max(1, keepdims=True)
            p = np.exp(z); p /= p.sum(1, keepdims=True)
            g = (p - Y) / len(Xtr)
            W -= lr * (Xtr.T @ g + 1e-4 * W); b -= lr * g.sum(0)
        acc = float(((Xte @ W + b).argmax(1) == yte).mean()) if len(Xte) else None
        majority = float((yte == np.bincount(ytr).argmax()).mean()) if len(Xte) else None
        top = np.abs(W).sum(1).argsort()[::-1][:10]
        return {"task": "classification", "model": "softmax regression on colour-histogram + thumbnail features" if "image_path" in df.columns else "softmax regression (numpy)", "target": target, "classes": len(classes),
                "train_rows": int(tr_mask.sum()), "test_rows": int((~tr_mask).sum()), "accuracy": acc, "majority_baseline": majority,
                "top_features": [names[i] for i in top]}
    y = y_raw.cast(pl.Float64).to_numpy()
    ytr, yte = y[tr_mask], y[~tr_mask]
    A = np.c_[Xtr, np.ones(len(Xtr))]
    w = np.linalg.solve(A.T @ A + 1e-2 * np.eye(A.shape[1]), A.T @ ytr)
    pred = np.c_[Xte, np.ones(len(Xte))] @ w
    ss = ((yte - yte.mean()) ** 2).sum()
    r2 = float(1 - ((yte - pred) ** 2).sum() / ss) if len(yte) and ss else None
    return {"task": "regression", "model": "ridge regression (numpy)", "target": target, "train_rows": int(tr_mask.sum()),
            "test_rows": int((~tr_mask).sum()), "r2": r2, "mae": float(np.abs(yte - pred).mean()) if len(yte) else None,
            "top_features": [names[i] for i in np.abs(w[:-1]).argsort()[::-1][:10]]}
