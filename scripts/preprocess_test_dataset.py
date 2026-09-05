#!/usr/bin/env python3
"""
Preprocess test patient NIfTI images + CSV data into a JSON dataset
for the web frontend and scheduler test endpoints.

Inputs:
  - NIfTI images: z-鹏城医疗模型代码/worker/new_pic/{pid}_c1_roi.nii.gz (DCE)
                  z-鹏城医疗模型代码/worker/new_pic/{pid}_dwi1_roi.nii.gz (DWI)
  - Clinical CSV: z-鹏城医疗模型代码/worker/new_dataset_csv/individual_clinical.csv
  - Radiomics CSV: z-鹏城医疗模型代码/worker/new_dataset_csv/individual_radiomics.csv

Output:
  - test/test_dataset.json  — array of preprocessed patient dicts

Usage:
  python scripts/preprocess_test_dataset.py
  python scripts/preprocess_test_dataset.py --output /path/to/output.json
  python scripts/preprocess_test_dataset.py --synthetic  # skip NIfTI, use random data
"""
import argparse
import csv
import gzip
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# ---- Paths ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NIFTI_DIR = PROJECT_ROOT / "z-鹏城医疗模型代码" / "worker" / "new_pic"
CLINICAL_CSV = PROJECT_ROOT / "z-鹏城医疗模型代码" / "worker" / "new_dataset_csv" / "individual_clinical.csv"
RADIOMICS_CSV = PROJECT_ROOT / "z-鹏城医疗模型代码" / "worker" / "new_dataset_csv" / "individual_radiomics.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "test" / "test_dataset.json"

IMG_SIZE = 224


# ---- Minimal NIfTI Reader (no nibabel dependency) ----
def read_nifti(filepath: str) -> np.ndarray:
    """Read a .nii.gz file and return the numpy array (F-order corrected)."""
    with gzip.open(filepath, 'rb') as f:
        hdr = f.read(348)
        # Parse dimensions
        dims = struct.unpack('<8h', hdr[40:56])
        ndim = dims[0]
        shape = tuple(dims[1:1 + ndim])
        # Parse datatype
        datatype = struct.unpack('<h', hdr[70:72])[0]
        dtype_map = {
            2: np.uint8, 4: np.int16, 8: np.int32,
            16: np.float32, 64: np.float64, 256: np.int8,
            512: np.uint16, 768: np.uint32, 1024: np.int64, 1280: np.uint64,
        }
        dt = dtype_map.get(datatype, np.float32)
        # Parse voxel offset
        vox_offset = struct.unpack('<f', hdr[108:112])[0]
        offset = int(vox_offset) if vox_offset > 0 else 348
        # Read raw data
        f.seek(0)
        all_data = f.read()
        raw = all_data[offset:]
        arr = np.frombuffer(raw, dtype=dt).reshape(shape, order='F')
        return arr.astype(np.float32)


def nifti_to_tensor(filepath: str, img_size: int = IMG_SIZE) -> list:
    """Load NIfTI → middle slice → normalize → resize → ToTensor → Normalize → nested list.

    Returns a 3-level nested list of shape [1, img_size, img_size] with values in ~[-1, 1].
    """
    arr = read_nifti(filepath)
    # Take middle slice if 3D
    if arr.ndim == 3:
        mid = arr.shape[2] // 2
        arr = arr[:, :, mid]
    # Normalize to [0, 255]
    arr_min, arr_max = arr.min(), arr.max()
    if arr_max > arr_min:
        arr = (arr - arr_min) / (arr_max - arr_min) * 255.0
    else:
        arr = np.zeros_like(arr)
    arr = arr.astype(np.uint8)
    # Convert to PIL, resize to 224x224
    img = Image.fromarray(arr, mode='L')
    # test transform: Resize(256) → CenterCrop(224)
    img = img.resize((256, 256), Image.BILINEAR)
    left = (256 - img_size) // 2
    top = (256 - img_size) // 2
    img = img.crop((left, top, left + img_size, top + img_size))
    # ToTensor (scales to [0, 1]) → Normalize(mean=0.5, std=0.5) (scales to [-1, 1])
    arr_float = np.array(img, dtype=np.float32) / 255.0
    arr_float = (arr_float - 0.5) / 0.5
    # Return as [1, H, W] nested list
    return [arr_float.tolist()]


# ---- CSV Reader (no pandas dependency) ----
def read_csv_columns(filepath: str, encoding: str = 'gbk') -> tuple:
    """Read a CSV file, return (headers, rows) where rows is list of dicts."""
    with open(filepath, 'r', encoding=encoding) as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [row for row in reader]
    return headers, rows


# ---- Clinical Preprocessing ----
# Clinical features expected by the model (23 features after preprocessing)
CLINICAL_NUMERIC = ['Ki_67', 'age', 'age_menarche', 'BMI']
CLINICAL_ORDINAL = ['HER2_status', 'T_stage', 'N_stage', 'M_stage']
CLINICAL_NOMINAL = ['PR_status', 'ER_status']
CLINICAL_RAW = [
    'NAC_classification', 'treatment_duration', 'NAC_treatment_cycle', 'endocrine',
    'Histological_type', 'result_ipsilateral_axillary_LNP',
    'enlargement_unilateral_axillary_LN', 'enlargement_unilateral_clavicular_LN',
    'enlargement_Ipsilateral_internal_mammary_LN',
    'target_lesion_location', 'target_lesion_quadrant'
]
CLINICAL_ALL = CLINICAL_NUMERIC + CLINICAL_ORDINAL + CLINICAL_NOMINAL + CLINICAL_RAW


def preprocess_clinical(rows: list) -> dict:
    """Preprocess clinical features for all patients.

    Returns dict mapping patient_id → clinical vector (23-dim).
    Uses simple scaling/encoding since we only have 10 patients.
    """
    # Collect values for normalization
    numeric_data = {col: [] for col in CLINICAL_NUMERIC}
    ordinal_data = {col: set() for col in CLINICAL_ORDINAL}
    nominal_data = {col: set() for col in CLINICAL_NOMINAL}

    patient_rows = {}
    for row in rows:
        pid = row.get('patient_ID', '').strip()
        try:
            numeric_data['Ki_67'].append(float(row.get('Ki_67', 0) or 0))
            numeric_data['age'].append(float(row.get('age', 0) or 0))
            numeric_data['age_menarche'].append(float(row.get('age_menarche', 0) or 0))
            numeric_data['BMI'].append(float(row.get('BMI', 0) or 0))
        except (ValueError, TypeError):
            pass
        for col in CLINICAL_ORDINAL:
            ordinal_data[col].add(row.get(col, '').strip())
        for col in CLINICAL_NOMINAL:
            nominal_data[col].add(row.get(col, '').strip())
        patient_rows[pid] = row

    # Compute means/stds for numeric
    num_means = {col: np.mean([v for v in vals if v == v]) for col, vals in numeric_data.items()}
    num_stds = {col: max(np.std([v for v in vals if v == v]), 1e-6) for col, vals in numeric_data.items()}

    # Build ordinal encoders
    ord_cats = {col: sorted([c for c in cats if c]) for col, cats in ordinal_data.items()}
    ord_map = {col: {c: i for i, c in enumerate(cats)} for col, cats in ord_cats.items()}

    # Build nominal encoders (one-hot)
    nom_cats = {col: sorted([c for c in cats if c]) for col, cats in nominal_data.items()}

    # Process each patient
    result = {}
    for pid, row in patient_rows.items():
        feats = []
        # Numeric: z-score
        for col in CLINICAL_NUMERIC:
            try:
                v = float(row.get(col, 0) or 0)
            except (ValueError, TypeError):
                v = num_means[col]
            feats.append((v - num_means[col]) / num_stds[col])
        # Ordinal: integer encoding
        for col in CLINICAL_ORDINAL:
            v = row.get(col, '').strip()
            feats.append(float(ord_map[col].get(v, -1)))
        # Nominal: one-hot (ER=2 cats, PR=2 cats = 4 dims)
        for col in CLINICAL_NOMINAL:
            v = row.get(col, '').strip()
            for cat in nom_cats[col]:
                feats.append(1.0 if v == cat else 0.0)
        # Raw: passthrough as float
        for col in CLINICAL_RAW:
            try:
                feats.append(float(row.get(col, 0) or 0))
            except (ValueError, TypeError):
                feats.append(0.0)
        result[pid] = feats

    # Verify dimension
    if result:
        sample_len = len(next(iter(result.values())))
        print(f"  Clinical feature dimension: {sample_len}")
    return result


def preprocess_radiomics(rows: list) -> dict:
    """Extract radiomics features (preDCE_ and preDWI_ prefixed columns).

    Returns dict mapping patient_id → radiomics vector (2264-dim).
    """
    # Find radiomics columns — match data_utils.py: only preDCE_ and preDWI_ prefixed columns
    rad_cols = [h for h in (rows[0].keys() if rows else [])
                if any(x in h for x in ['preDCE_', 'preDWI_'])]

    # Collect values per patient
    result = {}
    for row in rows:
        pid = row.get('patient_ID', '').strip()
        feats = []
        for col in rad_cols:
            try:
                feats.append(float(row.get(col, 0) or 0))
            except (ValueError, TypeError):
                feats.append(0.0)
        result[pid] = feats

    # Z-score normalize across patients
    if result:
        all_vecs = np.array(list(result.values()), dtype=np.float32)
        mean = all_vecs.mean(axis=0)
        std = np.maximum(all_vecs.std(axis=0), 1e-6)
        for pid in result:
            result[pid] = ((np.array(result[pid], dtype=np.float32) - mean) / std).tolist()

    if result:
        sample_len = len(next(iter(result.values())))
        print(f"  Radiomics feature dimension: {sample_len}")
    return result


# ---- Synthetic Image Generator (fallback) ----
def generate_synthetic_image(seed: int, img_size: int = IMG_SIZE) -> list:
    """Generate a synthetic grayscale image as [1, H, W] nested list in [-1, 1]."""
    rng = np.random.RandomState(seed)
    arr = rng.randn(img_size, img_size).astype(np.float32) * 0.5
    arr = np.clip(arr, -1.0, 1.0)
    return [arr.tolist()]


# ---- Main ----
def main():
    parser = argparse.ArgumentParser(description="Preprocess test patient dataset")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT),
                        help="Output JSON file path")
    parser.add_argument("--synthetic", action="store_true",
                        help="Use synthetic images instead of real NIfTI files")
    parser.add_argument("--nifti-dir", type=str, default=str(NIFTI_DIR),
                        help="Directory containing NIfTI files")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Preprocessing Test Patient Dataset")
    print("=" * 60)

    # 1. Load patient IDs from NIfTI directory
    nifti_dir = Path(args.nifti_dir)
    if nifti_dir.exists():
        dce_files = sorted(nifti_dir.glob("*_c1_roi.nii.gz"))
        # f.name = "1664718_c1_roi.nii.gz" → f.name.split("_c1_roi")[0] = "1664718"
        patient_ids = sorted(set(f.name.split("_c1_roi")[0] for f in dce_files))
    else:
        print(f"Warning: NIfTI directory {nifti_dir} not found")
        patient_ids = []

    # If no NIfTI, try reading CSVs for patient IDs
    if not patient_ids:
        print("  No NIfTI files found, extracting patient IDs from CSVs...")
        _, clinical_rows = read_csv_columns(str(CLINICAL_CSV))
        patient_ids = sorted(set(
            row.get('patient_ID', '').strip() for row in clinical_rows if row.get('patient_ID', '').strip()
        ))

    print(f"  Found {len(patient_ids)} patients: {patient_ids}")

    # 2. Load clinical data
    print("\n[1/4] Loading clinical data...")
    _, clinical_rows = read_csv_columns(str(CLINICAL_CSV))
    clinical_map = preprocess_clinical(clinical_rows)

    # 3. Load radiomics data
    print("\n[2/4] Loading radiomics data...")
    _, rad_rows = read_csv_columns(str(RADIOMICS_CSV))
    radiomics_map = preprocess_radiomics(rad_rows)

    # 4. Process images
    print(f"\n[3/4] Processing images (synthetic={args.synthetic})...")
    dataset = []

    for pid in patient_ids:
        entry = {
            "patient_id": pid,
            "bpCR": None,
            "hospital": None,
            "input": {},
        }

        # Get label from clinical data
        clin_row = next((r for r in clinical_rows if r.get('patient_ID', '').strip() == pid), {})
        try:
            entry["bpCR"] = int(clin_row.get('bpCR', -1))
        except (ValueError, TypeError):
            entry["bpCR"] = -1
        try:
            entry["hospital"] = int(clin_row.get('hospital', -1))
        except (ValueError, TypeError):
            entry["hospital"] = -1

        # Process images
        dce_path = nifti_dir / f"{pid}_c1_roi.nii.gz"
        dwi_path = nifti_dir / f"{pid}_dwi1_roi.nii.gz"

        if args.synthetic or not dce_path.exists():
            seed = hash(pid) % (2 ** 31)
            print(f"  {pid}: synthetic images (seed={seed})")
            dce_data = generate_synthetic_image(seed)
            dwi_data = generate_synthetic_image(seed + 1)
        else:
            print(f"  {pid}: processing NIfTI... ", end="", flush=True)
            try:
                dce_data = nifti_to_tensor(str(dce_path))
                dwi_data = nifti_to_tensor(str(dwi_path))
                print(f"done (DCE shape={len(dce_data)}×{len(dce_data[0])}×{len(dce_data[0][0])})")
            except Exception as e:
                print(f"FAILED: {e}")
                continue

        # Clinical features
        clinical = clinical_map.get(pid)
        if clinical is None:
            print(f"  {pid}: WARNING — no clinical data, using zeros")
            clinical = [0.0] * 23

        # Radiomics features
        radiomics = radiomics_map.get(pid)
        if radiomics is None:
            print(f"  {pid}: WARNING — no radiomics data, using zeros")
            radiomics = [0.0] * 2264

        entry["input"] = {
            "dce_image": dce_data,
            "dwi_image": dwi_data,
            "clinical": [clinical],
            "radiomics": [radiomics],
            "patient_ids": [pid],
        }
        dataset.append(entry)

    # 5. Save
    print(f"\n[4/4] Saving {len(dataset)} patients to {output_path}...")
    with open(output_path, 'w') as f:
        json.dump(dataset, f)

    file_size = output_path.stat().st_size
    print(f"  Done! File size: {file_size / 1024 / 1024:.1f} MB")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
