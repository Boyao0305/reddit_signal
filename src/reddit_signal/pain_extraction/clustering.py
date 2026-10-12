"""
Clustering module for pain objects (v3).

Pipeline: UMAP (384→50 dims) + HDBSCAN (leaf selection) + coherence-based splitting.
Auto-labels clusters from diverse member sampling.

Fixes from v2:
- Cluster ID mismatch: now maps HDBSCAN labels → DB IDs via id_map
- Noise stored as NULL, not -1
- HDBSCAN: leaf selection, min_cluster_size=10, min_samples=5
- UMAP: 50 dims, n_neighbors=15 for tighter local structure
- Large incoherent clusters are recursively split
"""

import logging
from typing import List, Optional, Tuple

import hdbscan
import numpy as np
import psycopg2.extras
from sklearn.metrics.pairwise import cosine_similarity

from . import db

logger = logging.getLogger(__name__)

# ─── Default parameters ──────────────────────────────────────────────────────

# FIX-3: Raised from 10→30 and 5→10. With 200K+ pain objects, clusters of 10
#         are noise artefacts, not real signal.
DEFAULT_MIN_CLUSTER_SIZE = 30
DEFAULT_MIN_SAMPLES = 10
DEFAULT_UMAP_N_COMPONENTS = 50
DEFAULT_UMAP_N_NEIGHBORS = 15
DEFAULT_UMAP_MIN_DIST = 0.0


# ─── UMAP dimensionality reduction ───────────────────────────────────────────

def reduce_dimensions(
    embeddings: np.ndarray,
    n_components: int = DEFAULT_UMAP_N_COMPONENTS,
    n_neighbors: int = DEFAULT_UMAP_N_NEIGHBORS,
    min_dist: float = DEFAULT_UMAP_MIN_DIST,
    # FIX-7: default changed False→True; reproducibility > speed at this stage.
    deterministic: bool = True,
) -> np.ndarray:
    """Reduce embedding dimensions with UMAP. Preserves local neighborhood.

    deterministic=True (default): sets random_state=42 for reproducible results.
    deterministic=False: omits random_state, enabling full UMAP parallelism.
    """
    import umap

    logger.info(
        "UMAP: %d points, %d→%d dims, n_neighbors=%d, min_dist=%.2f, deterministic=%s",
        len(embeddings), embeddings.shape[1], n_components,
        n_neighbors, min_dist, deterministic,
    )

    umap_kwargs = dict(
        n_components=n_components,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric="cosine",
        n_jobs=-1,
    )
    if deterministic:
        umap_kwargs["random_state"] = 42

    reducer = umap.UMAP(**umap_kwargs)
    reduced = reducer.fit_transform(embeddings)
    logger.info("UMAP done: shape %s", reduced.shape)
    return reduced


# ─── Clustering core ─────────────────────────────────────────────────────────

def run_hdbscan(
    embeddings: np.ndarray,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> np.ndarray:
    """
    Run HDBSCAN on embeddings (should be UMAP-reduced).
    Uses leaf selection for tighter clusters.
    Returns labels: (N,) array. -1 = noise.
    """
    logger.info(
        "HDBSCAN: %d points (dim=%d), min_cluster_size=%d, min_samples=%d, method=leaf",
        len(embeddings), embeddings.shape[1], min_cluster_size, min_samples,
    )

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        cluster_selection_method="leaf",
        cluster_selection_epsilon=0.0,
        core_dist_n_jobs=-1,
    )
    labels = clusterer.fit_predict(embeddings)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = np.sum(labels == -1)
    logger.info("HDBSCAN result: %d clusters, %d noise points (%.1f%%)",
                n_clusters, n_noise, 100 * n_noise / len(labels))

    return labels


# ─── Cluster properties ──────────────────────────────────────────────────────

def compute_cluster_centroids(
    embeddings: np.ndarray,
    labels: np.ndarray,
) -> dict:
    """Compute centroid (mean embedding) for each cluster. Excludes noise (-1)."""
    centroids = {}
    unique_labels = set(labels)
    unique_labels.discard(-1)

    for cid in unique_labels:
        mask = labels == cid
        cluster_vecs = embeddings[mask]
        centroid = cluster_vecs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        centroids[int(cid)] = centroid

    return centroids


def auto_label_clusters(
    ids: List[int],
    embeddings: np.ndarray,
    labels: np.ndarray,
    centroids: dict,
    top_k: int = 5,
) -> dict:
    """
    Generate labels by finding diverse top_k pain statements per cluster.
    Uses maximal marginal relevance (MMR) for diversity:
    - High similarity to centroid (relevance)
    - Low similarity to already-selected statements (diversity)
    """
    # Fetch pain statements for all IDs
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, pain_statement FROM pain_objects
                WHERE id = ANY(%s)
            """, (ids,))
            id_to_text = {r["id"]: r["pain_statement"] for r in cur.fetchall()}
    finally:
        db.put_conn(conn)

    cluster_labels = {}
    for cid, centroid in centroids.items():
        mask = labels == cid
        cluster_indices = np.where(mask)[0]
        cluster_vecs = embeddings[cluster_indices]

        # Cosine similarity to centroid
        centroid_sims = cosine_similarity(
            cluster_vecs, centroid.reshape(1, -1)
        ).flatten()

        # MMR selection for diversity
        selected = []
        remaining = list(range(len(cluster_indices)))
        k = min(top_k, len(remaining))

        for _ in range(k):
            if not remaining:
                break
            if not selected:
                # First pick: highest similarity to centroid
                best = max(remaining, key=lambda i: centroid_sims[i])
            else:
                # MMR: balance relevance (centroid sim) and diversity (min sim to selected)
                selected_vecs = cluster_vecs[selected]
                best_score = -1
                best = remaining[0]
                for i in remaining:
                    relevance = centroid_sims[i]
                    # Max similarity to any already-selected
                    diversity_penalty = cosine_similarity(
                        cluster_vecs[i].reshape(1, -1), selected_vecs
                    ).max()
                    mmr = 0.5 * relevance - 0.5 * diversity_penalty
                    if mmr > best_score:
                        best_score = mmr
                        best = i
            selected.append(best)
            remaining.remove(best)

        # Build label from selected statements
        statements = []
        for idx in selected:
            original_idx = cluster_indices[idx]
            pid = ids[original_idx]
            text = id_to_text.get(pid, "?")
            if len(text) > 100:
                text = text[:97] + "..."
            statements.append(text)

        cluster_labels[cid] = " | ".join(statements)

    return cluster_labels


def compute_cluster_coherence(
    embeddings: np.ndarray,
    labels: np.ndarray,
) -> dict:
    """Average pairwise cosine similarity within each cluster."""
    coherences = {}
    unique_labels = set(labels)
    unique_labels.discard(-1)

    for cid in unique_labels:
        mask = labels == cid
        cluster_vecs = embeddings[mask]
        n = len(cluster_vecs)

        if n < 2:
            coherences[int(cid)] = 1.0
            continue

        if n > 200:
            sample_idx = np.random.choice(n, 200, replace=False)
            cluster_vecs = cluster_vecs[sample_idx]

        sim_matrix = cosine_similarity(cluster_vecs)
        upper = sim_matrix[np.triu_indices_from(sim_matrix, k=1)]
        coherences[int(cid)] = float(np.mean(upper))

    return coherences


# ─── Large cluster splitting ─────────────────────────────────────────────────

def split_large_clusters(
    ids: List[int],
    embeddings_full: np.ndarray,
    labels: np.ndarray,
    coherences: dict,
    coherence_threshold: float = 0.5,
    size_threshold: int = 40,
    sub_min_cluster_size: int = 5,
) -> np.ndarray:
    """
    For clusters below coherence_threshold with more than size_threshold members,
    run a second-pass HDBSCAN on just those members' full embeddings.
    """
    new_labels = labels.copy()
    next_new_id = int(labels.max()) + 1
    splits_done = 0

    for cid, coherence in coherences.items():
        member_count = int(np.sum(labels == cid))
        if coherence >= coherence_threshold or member_count <= size_threshold:
            continue

        mask = labels == cid
        sub_indices = np.where(mask)[0]
        sub_embeddings = embeddings_full[sub_indices]

        # UMAP on sub-cluster
        import umap
        n_comp = min(15, member_count - 2)
        n_neigh = min(10, member_count // 3)
        if n_comp < 2 or n_neigh < 2:
            continue

        try:
            sub_reduced = umap.UMAP(
                n_components=n_comp,
                n_neighbors=n_neigh,
                min_dist=0.0,
                metric="cosine",
                random_state=42,
            ).fit_transform(sub_embeddings)

            sub_clusterer = hdbscan.HDBSCAN(
                min_cluster_size=sub_min_cluster_size,
                min_samples=2,
                metric="euclidean",
                cluster_selection_method="leaf",
            )
            sub_labels = sub_clusterer.fit_predict(sub_reduced)
        except Exception as e:
            logger.warning("Failed to split cluster %d: %s", cid, e)
            continue

        n_sub = len(set(sub_labels)) - (1 if -1 in sub_labels else 0)
        if n_sub <= 1:
            continue  # no meaningful split found

        # Remap sub-labels to globally unique IDs
        for i, sub_label in enumerate(sub_labels):
            orig_idx = sub_indices[i]
            if sub_label == -1:
                new_labels[orig_idx] = -1
            else:
                new_labels[orig_idx] = next_new_id + sub_label

        next_new_id += int(sub_labels.max()) + 1
        splits_done += 1
        n_noise_sub = int(np.sum(sub_labels == -1))
        logger.info("Split cluster %d (%d members, coh=%.3f) → %d sub-clusters + %d noise",
                     cid, member_count, coherence, n_sub, n_noise_sub)

    logger.info("Split %d large incoherent clusters.", splits_done)
    return new_labels


# ─── DB operations ────────────────────────────────────────────────────────────

def save_cluster_assignments(ids: List[int], labels: np.ndarray, id_map: dict = None):
    """
    DEPRECATED in Phase 1 v2: pain_objects.cluster_id was dropped, so we no
    longer write to it. Use the run-based path (cluster_all with run_id) which
    writes to pain_cluster_members. This function is a no-op kept only for
    backward import compatibility.
    """
    logger.warning(
        "save_cluster_assignments() is deprecated — pain_objects.cluster_id was "
        "dropped in Phase 1 v2. Use the run-based path (cluster_all with run_id)."
    )
    return


def save_clusters(
    centroids: dict,
    cluster_labels: dict,
    coherences: dict,
    labels: np.ndarray,
    run_id: Optional[int] = None,
) -> dict:
    """
    Insert rows into pain_clusters table. run_id is required in v2 — the legacy
    destructive rebuild path was removed when pain_objects.cluster_id was dropped.
    Returns {hdbscan_label: db_id} mapping.
    """
    if run_id is None:
        raise ValueError(
            "save_clusters() requires run_id in Phase 1 v2. The legacy destructive "
            "rebuild path is removed. Create an analysis_runs row first."
        )
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            id_map = {}
            for cid in sorted(centroids.keys()):
                member_count = int(np.sum(labels == cid))
                label_text = cluster_labels.get(cid, "")
                coherence = coherences.get(cid, 0.0)
                centroid_str = "[" + ",".join(
                    f"{v:.6f}" for v in centroids[cid]
                ) + "]"

                if run_id is not None:
                    cur.execute("""
                        INSERT INTO pain_clusters (
                            run_id, label, member_count, coherence, centroid
                        ) VALUES (%s, %s, %s, %s, %s::vector)
                        RETURNING id
                    """, (run_id, label_text, member_count, coherence, centroid_str))
                else:
                    cur.execute("""
                        INSERT INTO pain_clusters (
                            label, member_count, coherence, centroid
                        ) VALUES (%s, %s, %s, %s::vector)
                        RETURNING id
                    """, (label_text, member_count, coherence, centroid_str))
                db_id = cur.fetchone()[0]
                id_map[cid] = db_id

        conn.commit()
        logger.info("Saved %d clusters to pain_clusters table (run_id=%s).", len(id_map), run_id)
        return id_map
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


def save_cluster_members(ids: List[int], labels: np.ndarray, id_map: dict):
    """
    Insert rows into pain_cluster_members junction table.
    Used by the run-based analysis pipeline (non-destructive).
    Noise points (-1) are skipped.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            data = []
            for i, pain_id in enumerate(ids):
                hdbscan_label = int(labels[i])
                if hdbscan_label == -1:
                    continue
                db_cluster_id = id_map.get(hdbscan_label)
                if db_cluster_id is None:
                    continue
                data.append((pain_id, db_cluster_id))

            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO pain_cluster_members (pain_id, cluster_id)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                data,
                page_size=1000,
            )
        conn.commit()
        logger.info("Saved %d pain_cluster_members entries.", len(data))
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


def create_clusters_table():
    """Create pain_clusters table if it doesn't exist."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pain_clusters (
                    id                  SERIAL PRIMARY KEY,
                    label               TEXT,
                    member_count        INT NOT NULL,
                    intensity           FLOAT,
                    prevalence          FLOAT,
                    coherence           FLOAT,
                    competitive_density FLOAT,
                    opportunity_score   FLOAT,
                    centroid            VECTOR(384),
                    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
        conn.commit()
        logger.info("pain_clusters table ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


# ─── Main entry ───────────────────────────────────────────────────────────────

def cluster_all(
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    umap_dims: int = DEFAULT_UMAP_N_COMPONENTS,
    umap_neighbors: int = DEFAULT_UMAP_N_NEIGHBORS,
    split_incoherent: bool = True,
    window_start=None,
    window_end=None,
    run_id: Optional[int] = None,
    # FIX-7: default changed False→True; reproducibility > speed at this stage.
    deterministic: bool = True,
) -> dict:
    """
    Full clustering pipeline:
    1. Load embeddings from DB (filtered by window if run_id given)
    2. UMAP dimensionality reduction (384 → 50)
    3. HDBSCAN on reduced embeddings (leaf selection)
    4. Split large incoherent clusters
    5. Compute centroids (on original 384-dim), labels, coherence
    6. Save to DB with correct ID mapping

    When run_id is provided: non-destructive, uses pain_cluster_members junction table.
    When run_id is None: legacy destructive rebuild (old --cluster behavior).
    """
    from . import embedding as emb

    if run_id is not None and window_start is not None and window_end is not None:
        print(f"Loading embeddings for window {window_start} → {window_end}...")
        ids, embeddings_full = emb.load_embeddings_for_window(window_start, window_end)
    else:
        print("Loading embeddings from DB...")
        ids, embeddings_full = emb.load_all_embeddings()

    if len(ids) == 0:
        print("No embeddings found. Run --embed first.")
        return {"clusters": 0, "noise": 0}

    print(f"Loaded {len(ids)} embeddings (dim={embeddings_full.shape[1]}).")

    # Ensure clusters table exists
    create_clusters_table()

    # UMAP reduction
    print(f"Reducing dimensions: {embeddings_full.shape[1]} → {umap_dims}...")
    embeddings_reduced = reduce_dimensions(
        embeddings_full,
        n_components=umap_dims,
        n_neighbors=umap_neighbors,
        deterministic=deterministic,
    )

    # Run HDBSCAN on reduced embeddings
    labels = run_hdbscan(embeddings_reduced, min_cluster_size, min_samples)

    n_clusters_initial = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise_initial = int(np.sum(labels == -1))
    print(f"Initial: {n_clusters_initial} clusters, {n_noise_initial} noise "
          f"({100*n_noise_initial/len(labels):.1f}%)")

    # Split large incoherent clusters
    if split_incoherent:
        # FIX-6: Use UMAP-reduced embeddings for the split decision.
        # Clusters were formed in reduced space, so coherence in that space
        # is the right signal for "is this cluster tight enough?".
        # The FINAL coherence (saved to DB below) still uses full 384-dim embeddings
        # since that is the canonical representation for stored coherence values.
        print("Computing coherence for split decision (UMAP-reduced space)...")
        pre_coherences = compute_cluster_coherence(embeddings_reduced, labels)
        n_candidates = sum(1 for cid, coh in pre_coherences.items()
                          if coh < 0.5 and np.sum(labels == cid) > 40)
        print(f"Splitting {n_candidates} large incoherent clusters...")
        labels = split_large_clusters(
            ids, embeddings_full, labels, pre_coherences,
            coherence_threshold=0.5, size_threshold=40,
        )

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    print(f"After splitting: {n_clusters} clusters, {n_noise} noise "
          f"({100*n_noise/len(labels):.1f}%)")

    # Compute final cluster properties on ORIGINAL 384-dim embeddings
    print("Computing centroids (on full 384-dim embeddings)...")
    centroids = compute_cluster_centroids(embeddings_full, labels)

    print("Auto-labeling clusters (MMR diversity)...")
    cluster_labels = auto_label_clusters(ids, embeddings_full, labels, centroids)

    print("Computing coherence scores (on full 384-dim embeddings)...")
    coherences = compute_cluster_coherence(embeddings_full, labels)

    # Save clusters to DB first, get id_map
    print("Saving clusters to pain_clusters table...")
    id_map = save_clusters(centroids, cluster_labels, coherences, labels, run_id=run_id)

    if run_id is not None:
        # Non-destructive: write to pain_cluster_members junction table
        print("Saving cluster members to pain_cluster_members...")
        save_cluster_members(ids, labels, id_map=id_map)
    else:
        # Legacy: update pain_objects.cluster_id
        print("Saving cluster assignments to pain_objects (with correct ID mapping)...")
        save_cluster_assignments(ids, labels, id_map=id_map)

    # Print cluster summary
    print(f"\n── Cluster Summary ({'─' * 50}")
    sizes = sorted(
        [(cid, int(np.sum(labels == cid))) for cid in centroids.keys()],
        key=lambda x: x[1], reverse=True,
    )
    for cid, size in sizes[:20]:
        coh = coherences.get(cid, 0)
        db_id = id_map.get(cid, "?")
        label = cluster_labels.get(cid, "")[:80]
        print(f"  Cluster {db_id:>4} (hdb={cid:>3d}): {size:>4d} members, "
              f"coherence={coh:.3f} — {label}")
    if len(sizes) > 20:
        print(f"  ... and {len(sizes) - 20} more clusters")
    print("─" * 60)

    # Coherence distribution
    coh_values = list(coherences.values())
    low = sum(1 for c in coh_values if c < 0.5)
    med = sum(1 for c in coh_values if 0.5 <= c < 0.7)
    high = sum(1 for c in coh_values if c >= 0.7)
    print(f"\nCoherence: <0.5: {low} ({100*low/len(coh_values):.0f}%) | "
          f"0.5-0.7: {med} ({100*med/len(coh_values):.0f}%) | "
          f"≥0.7: {high} ({100*high/len(coh_values):.0f}%)")

    # FIX-8: Global quality metric — Davies-Bouldin Index on UMAP-reduced embeddings.
    # DBI is cheaper than silhouette on large datasets.
    # Lower DBI = better-separated, tighter clusters.
    result: dict = {
        "clusters": n_clusters,
        "noise": n_noise,
        "total": len(ids),
        "cluster_sizes": sizes,
        "id_map": id_map,
    }

    non_noise_mask = labels != -1
    labels_no_noise = labels[non_noise_mask]
    unique_non_noise = np.unique(labels_no_noise)
    if len(unique_non_noise) >= 2:
        try:
            from sklearn.metrics import davies_bouldin_score
            embeddings_reduced_no_noise = embeddings_reduced[non_noise_mask]
            dbi = davies_bouldin_score(embeddings_reduced_no_noise, labels_no_noise)
            print(f"\nDavies-Bouldin Index (UMAP space): {dbi:.4f}  (lower = better clustering)")
            result["davies_bouldin_index"] = dbi
        except Exception as e:
            logger.warning("Could not compute Davies-Bouldin Index: %s", e)
    else:
        print("\nSkipping Davies-Bouldin Index (< 2 non-noise clusters found)")

    return result
