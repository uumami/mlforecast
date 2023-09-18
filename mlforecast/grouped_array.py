# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/grouped_array.ipynb.

# %% auto 0
__all__ = ['GroupedArray']

# %% ../nbs/grouped_array.ipynb 1
from typing import TYPE_CHECKING, Callable, Tuple, Union

if TYPE_CHECKING:
    import pandas as pd
import numpy as np
from numba import njit
from window_ops.shift import shift_array

# %% ../nbs/grouped_array.ipynb 2
@njit(nogil=True)
def _transform_series(data, indptr, updates_only, lag, func, *args) -> np.ndarray:
    """Shifts every group in `data` by `lag` and computes `func(shifted, *args)`.

    If `updates_only=True` only last value of the transformation for each group is returned,
    otherwise the full transformation is returned"""
    n_series = len(indptr) - 1
    if updates_only:
        out = np.empty_like(data[:n_series])
        for i in range(n_series):
            lagged = shift_array(data[indptr[i] : indptr[i + 1]], lag)
            out[i] = func(lagged, *args)[-1]
    else:
        out = np.empty_like(data)
        for i in range(n_series):
            lagged = shift_array(data[indptr[i] : indptr[i + 1]], lag)
            out[indptr[i] : indptr[i + 1]] = func(lagged, *args)
    return out


@njit
def _diff(x, lag):
    y = x.copy()
    for i in range(lag):
        y[i] = np.nan
    for i in range(lag, x.size):
        y[i] = x[i] - x[i - lag]
    return y


@njit
def _apply_difference(data, indptr, new_data, new_indptr, d):
    n_series = len(indptr) - 1
    for i in range(n_series):
        new_data[new_indptr[i] : new_indptr[i + 1]] = data[
            indptr[i + 1] - d : indptr[i + 1]
        ]
        sl = slice(indptr[i], indptr[i + 1])
        data[sl] = _diff(data[sl], d)


@njit
def _restore_difference(preds, data, indptr, d):
    n_series = len(indptr) - 1
    h = len(preds) // n_series
    for i in range(n_series):
        s = data[indptr[i] : indptr[i + 1]]
        for j in range(min(h, d)):
            preds[i * h + j] += s[j]
        for j in range(d, h):
            preds[i * h + j] += preds[i * h + j - d]


@njit
def _restore_fitted_difference(diffs_data, diffs_indptr, data, indptr, d):
    n_series = len(indptr) - 1
    for i in range(n_series):
        serie = data[indptr[i] : indptr[i + 1]]
        diffs_size = diffs_indptr[i + 1] - diffs_indptr[i]
        dropped_rows = diffs_size - serie.size
        start_idx = max(0, d - dropped_rows)
        for j in range(start_idx, serie.size):
            serie[j] += diffs_data[diffs_indptr[i + 1] - serie.size - d + j]


@njit
def _expand_target(data, indptr, max_horizon):
    out = np.empty((data.size, max_horizon), dtype=data.dtype)
    n_series = len(indptr) - 1
    n = 0
    for i in range(n_series):
        serie = data[indptr[i] : indptr[i + 1]]
        for j in range(serie.size):
            upper = min(serie.size - j, max_horizon)
            for k in range(upper):
                out[n, k] = serie[j + k]
            for k in range(upper, max_horizon):
                out[n, k] = np.nan
            n += 1
    return out


@njit
def _append_one(
    data: np.ndarray, indptr: np.ndarray, new: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Append each value of new to each group in data formed by indptr."""
    n_series = len(indptr) - 1
    new_data = np.empty(data.size + new.size, dtype=data.dtype)
    new_indptr = indptr.copy()
    new_indptr[1:] += np.arange(1, n_series + 1)
    for i in range(n_series):
        prev_slice = slice(indptr[i], indptr[i + 1])
        new_slice = slice(new_indptr[i], new_indptr[i + 1] - 1)
        new_data[new_slice] = data[prev_slice]
        new_data[new_indptr[i + 1] - 1] = new[i]
    return new_data, new_indptr


@njit
def _append_several(
    data: np.ndarray,
    indptr: np.ndarray,
    new_sizes: np.ndarray,
    new_values: np.ndarray,
    new_groups: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    new_data = np.empty(data.size + new_values.size, dtype=data.dtype)
    new_indptr = np.empty(new_sizes.size + 1, dtype=indptr.dtype)
    new_indptr[0] = 0
    old_indptr_idx = 0
    new_vals_idx = 0
    for i, is_new in enumerate(new_groups):
        new_size = new_sizes[i]
        if is_new:
            old_size = 0
        else:
            prev_slice = slice(indptr[old_indptr_idx], indptr[old_indptr_idx + 1])
            old_indptr_idx += 1
            old_size = prev_slice.stop - prev_slice.start
            new_size += old_size
            new_data[new_indptr[i] : new_indptr[i] + old_size] = data[prev_slice]
        new_indptr[i + 1] = new_indptr[i] + new_size
        new_data[new_indptr[i] + old_size : new_indptr[i + 1]] = new_values[
            new_vals_idx : new_vals_idx + new_sizes[i]
        ]
        new_vals_idx += new_sizes[i]
    return new_data, new_indptr

# %% ../nbs/grouped_array.ipynb 4
class GroupedArray:
    """Array made up of different groups. Can be thought of (and iterated) as a list of arrays.

    All the data is stored in a single 1d array `data`.
    The indices for the group boundaries are stored in another 1d array `indptr`."""

    def __init__(self, data: np.ndarray, indptr: np.ndarray):
        self.data = data
        self.indptr = indptr
        self.ngroups = len(indptr) - 1

    def __len__(self) -> int:
        return self.ngroups

    def __getitem__(self, idx: int) -> np.ndarray:
        return self.data[self.indptr[idx] : self.indptr[idx + 1]]

    def __setitem__(self, idx: int, vals: np.ndarray):
        if self[idx].size != vals.size:
            raise ValueError(f"vals must be of size {self[idx].size}")
        self[idx][:] = vals

    def take(self, idxs: np.ndarray) -> "GroupedArray":
        ranges = [range(self.indptr[i], self.indptr[i + 1]) for i in idxs]
        items = [self.data[rng] for rng in ranges]
        sizes = np.array([item.size for item in items])
        data = np.hstack(items)
        indptr = np.append(0, sizes.cumsum())
        return GroupedArray(data, indptr)

    @classmethod
    def from_sorted_df(
        cls, df: "pd.DataFrame", id_col: str, target_col: str
    ) -> "GroupedArray":
        sizes = df.groupby(id_col, observed=True).size().values
        indptr = np.append(0, sizes.cumsum())
        data = df[target_col].values.copy()
        if data.dtype not in (np.float32, np.float64):
            # since all transformations generate nulls, we need a float dtype
            data = data.astype(np.float32)
        return cls(data, indptr)

    def transform_series(
        self, updates_only: bool, lag: int, func: Callable, *args
    ) -> np.ndarray:
        return _transform_series(self.data, self.indptr, updates_only, lag, func, *args)

    def restore_difference(self, preds: np.ndarray, d: int) -> None:
        _restore_difference(preds, self.data, self.indptr, d)

    def restore_fitted_difference(
        self, series_data: np.ndarray, series_indptr: np.ndarray, d: int
    ) -> None:
        if len(self.indptr) != len(series_indptr):
            raise ValueError("Found different number of groups in fitted differences.")
        _restore_fitted_difference(
            self.data,
            self.indptr,
            series_data,
            series_indptr,
            d,
        )

    def expand_target(self, max_horizon: int) -> np.ndarray:
        return _expand_target(self.data, self.indptr, max_horizon)

    def take_from_groups(self, idx: Union[int, slice]) -> "GroupedArray":
        """Takes `idx` from each group in the array."""
        ranges = [
            range(self.indptr[i], self.indptr[i + 1])[idx] for i in range(self.ngroups)
        ]
        items = [self.data[rng] for rng in ranges]
        sizes = np.array([item.size for item in items])
        data = np.hstack(items)
        indptr = np.append(0, sizes.cumsum())
        return GroupedArray(data, indptr)

    def append(self, new: np.ndarray) -> "GroupedArray":
        """Appends each element of `new` to each existing group. Returns a copy."""
        if new.size != self.ngroups:
            raise ValueError(f"new must be of size {self.ngroups}")
        new_data, new_indptr = _append_one(self.data, self.indptr, new)
        return GroupedArray(new_data, new_indptr)

    def append_several(
        self, new_sizes: np.ndarray, new_values: np.ndarray, new_groups: np.ndarray
    ) -> "GroupedArray":
        new_data, new_indptr = _append_several(
            self.data, self.indptr, new_sizes, new_values, new_groups
        )
        return GroupedArray(new_data, new_indptr)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(ndata={self.data.size}, ngroups={self.ngroups})"
        )
